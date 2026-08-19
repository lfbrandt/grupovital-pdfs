"""
compress_service.py — Grupo Vital PDFs

Nota sobre parâmetros do Ghostscript:
  Não usamos -dPDFSETTINGS porque ele define ColorACSImageDict internamente,
  podendo sobrescrever parâmetros externos dependendo da versão/build do GS.
  Parâmetros de imagem são passados via setdistillerparams (PostScript inline),
  que é a forma portável e confiável para controlar qualidade JPEG e resolução.

Nota sobre QFactor:
  O GS usa QFactor (0.0–1.0) no distiller, não JPEG Q (0–100).
  Curva monotônica ancorada no equivalente documentado JPEGQ=75 → QFactor=0.5:
  QFactor = sqrt(1.0 - jpeg_q / 100.0)
  Q=88 → QF=0.346  Q=72 → QF=0.529  Q=45 → QF=0.742
  HSamples/VSamples [1 1 1 1] desativam chroma subsampling.

Nota sobre resolução efetiva:
  color_res = min(dpi, cap_da_faixa)
  Se dpi < cap → dpi domina. Se dpi > cap → cap domina.
  Exemplo: quality=80 (cap=200), dpi=100 → color_res=100 (não 200).
"""
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter  # type: ignore

from flask import current_app, has_app_context

from app.services.sandbox import run_in_sandbox

# ── Configuração ──────────────────────────────────────────────────────────────
GHOSTSCRIPT_TIMEOUT = int(os.environ.get('GS_TIMEOUT',   '120'))
QPDF_TIMEOUT        = int(os.environ.get('QPDF_TIMEOUT', '60'))

try:
    from app.services.sanitize_service import sanitize_pdf_preserving_content
    _HAS_SANITIZE = True
except ImportError:
    _HAS_SANITIZE = False

from app.utils.pdf_utils import (
    pdf_preservation_warnings,
    pdf_requires_content_preservation,
    replace_pdf_pages_preserving_catalog,
    write_preserving_pdf_subset,
)

try:
    from app.utils.pdf_utils import page_count as _ext_page_count
    _HAS_PDF_UTILS = True
except ImportError:
    _HAS_PDF_UTILS = False


# ── Helpers ───────────────────────────────────────────────────────────────────
import logging as _logging
_gs_log = _logging.getLogger(__name__)

# Cache do binário resolvido — evita chamar shutil.which() repetidamente.
_GS_CMD_CACHE: str | None = None


def _get_gs_cmd() -> str:
    """
    Resolve o binário do Ghostscript na seguinte ordem de prioridade:

      1. GS_BIN          — nome canônico do projeto (merge_service, converter_service)
      2. GHOSTSCRIPT_BIN — nome documentado no .env.example, README e Dockerfile
      3. GS_PATH         — alias legado (compress_service anterior); mantido por compatibilidade
      4. gswin64c        — Windows 64-bit (auto-detect via shutil.which)
      5. gswin32c        — Windows 32-bit (auto-detect via shutil.which)
      6. gs              — Linux/macOS   (auto-detect via shutil.which)
      7. 'gs'            — fallback cego (vai lançar FileNotFoundError em runtime)

    O resultado é cacheado após a primeira resolução bem-sucedida.
    O caminho absoluto resolvido e a versão do GS são logados uma única vez (INFO).
    Nenhum dado de PDF, payload ou usuário é logado aqui.
    """
    global _GS_CMD_CACHE
    if _GS_CMD_CACHE is not None:
        return _GS_CMD_CACHE

    source = None

    # Passos 1–3: variáveis de ambiente, do mais específico ao alias legado.
    # Ordem padronizada com o resto do projeto (GS_BIN / GHOSTSCRIPT_BIN primeiro).
    for env_var in ('GS_BIN', 'GHOSTSCRIPT_BIN', 'GS_PATH'):
        val = os.environ.get(env_var, '').strip()
        if not val:
            continue
        resolved = shutil.which(val)
        if resolved:
            _GS_CMD_CACHE = resolved
            source = f'env:{env_var}'
            break
        # Variável definida mas binário não encontrável — avisa e tenta a próxima.
        _gs_log.warning(
            '[gs-resolve] %s="%s" definido mas não localizável via shutil.which — '
            'verifique o caminho e tente novamente',
            env_var, val,
        )

    # Passos 4–6: detecção automática por nome canônico de plataforma.
    if _GS_CMD_CACHE is None:
        for candidate in ('gswin64c', 'gswin32c', 'gs'):
            resolved = shutil.which(candidate)
            if resolved:
                _GS_CMD_CACHE = resolved
                source = f'auto-detect:{candidate}'
                break

    # Passo 7: fallback cego — nunca vai funcionar se chegou aqui sem resolver.
    if _GS_CMD_CACHE is None:
        _GS_CMD_CACHE = 'gs'
        source = 'fallback-blind'
        _gs_log.error(
            '[gs-resolve] Ghostscript NÃO encontrado no PATH nem via variáveis de ambiente. '
            'Defina GS_BIN ou GHOSTSCRIPT_BIN com o caminho completo do executável. '
            'A próxima chamada ao GS vai lançar FileNotFoundError.'
        )

    _gs_log.info('[gs-resolve] binário=ghostscript fonte=%s', source)

    # Loga a versão do GS para diagnóstico de ambiente (sem dados de usuário).
    try:
        working_parent = (
            current_app.config.get("UPLOAD_FOLDER")
            if has_app_context()
            else tempfile.gettempdir()
        )
        working_parent = os.path.abspath(
            os.fspath(working_parent or tempfile.gettempdir())
        )
        os.makedirs(working_parent, exist_ok=True)
        ver_result = run_ghostscript_command(
            [_GS_CMD_CACHE, '--version'],
            working_parent=working_parent,
            timeout=5,
        )
        gs_version = ver_result.stdout.strip() or ver_result.stderr.strip() or '?'
        _gs_log.info('[gs-resolve] versão=%s', gs_version)
    except Exception as _ver_err:
        _gs_log.warning('[gs-resolve] não foi possível obter versão do GS: %s', type(_ver_err).__name__)

    return _GS_CMD_CACHE


def _get_qpdf_cmd():
    return shutil.which('qpdf')


# ── Helpers de diagnóstico de comando ─────────────────────────────────────────
import re as _re
import shlex as _shlex

def _mask_upload_path(token: str) -> str:
    """
    Substitui o basename de caminhos temporários de upload por um placeholder
    mantendo apenas a extensão, para não vazar UUIDs de sessão no log.

    Exemplos:
      /tmp/upload_abc123.pdf          → <upload>.pdf
      C:\\uploads\\flat_def456.pdf    → <flat>.pdf
      /tmp/rotated_xyz.pdf            → <rotated>.pdf
    Tokens que não são caminhos de upload são devolvidos intactos.
    """
    if token.startswith('-sOutputFile='):
        return '-sOutputFile=' + _mask_upload_path(token.split('=', 1)[1])

    normalized = token.replace('\\', '/').lower()
    looks_like_path = bool(_re.search(r'[\\/]', token)) or token.startswith('.')
    if looks_like_path and normalized.rsplit('/', 1)[-1] in {
        'gs', 'gs.exe', 'gswin32c.exe', 'gswin64c.exe',
    }:
        return '<ghostscript>'
    path_markers = (
        'upload', 'flat', 'sanitized', 'rot', 'extracted', 'rotated',
        'comprimido', 'group', 'merged', 'selected_baseline', 'gs_thumb',
        'target_candidate', 'candidate', 'tmp', 'temp',
    )
    if not looks_like_path or not any(marker in normalized for marker in path_markers):
        return token
    # extrai prefixo semântico (upload, flat, comprimido, …) para o placeholder
    basename = _re.split(r'[/\\]', token)[-1]          # e.g. "flat_def456.pdf"
    prefix   = _re.split(r'[_.]', basename)[0]          # e.g. "flat"
    ext      = '.' + basename.rsplit('.', 1)[-1] if '.' in basename else ''
    return f'<{prefix}>{ext}'


def _fmt_gs_cmd(args: list) -> str:
    """
    Formata a lista de args do Ghostscript para uma string legível e segura
    adequada para log INFO.

    Comportamento:
    - Tokens que são caminhos de upload são mascarados via _mask_upload_path().
    - O bloco -c <distiller_ps> é mantido intacto (contém apenas parâmetros
      técnicos sem dados de usuário).
    - Usa shlex.join() em Linux/macOS e subprocess.list2cmdline() em Windows
      para que o comando copiado diretamente do log seja executável na plataforma
      onde foi gerado.
    """
    import sys as _sys
    masked = [_mask_upload_path(a) for a in args]
    if _sys.platform == 'win32':
        import subprocess as _sp
        return _sp.list2cmdline(masked)
    return _shlex.join(masked)


def _page_count(path: str) -> int:
    if _HAS_PDF_UTILS:
        try:
            return _ext_page_count(path)
        except Exception:
            pass
    try:
        with open(path, 'rb') as f:
            return len(PdfReader(f).pages)
    except Exception:
        return 0


# ── Validação de segurança pós-compressão ─────────────────────────────────────

def _cleanup_paths(paths) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def count_pdf_pages(path: str) -> int:
    """Retorna número de páginas do PDF em `path`. Retorna 0 se não legível."""
    return _page_count(path)


def validate_pdf_readable(path: str) -> bool:
    """Tenta abrir o PDF e confirma que tem ao menos 1 página. False se falhar."""
    try:
        with open(path, 'rb') as f:
            r = PdfReader(f)
            return len(r.pages) > 0
    except Exception:
        return False


def _visual_page_size(page) -> tuple[float, float]:
    box = page.get('/CropBox') or page.get('/MediaBox')
    if box is None or len(box) != 4:
        raise ValueError('page_box_missing')
    width = abs(float(box[2]) - float(box[0]))
    height = abs(float(box[3]) - float(box[1]))
    rotation = int(page.get('/Rotate', 0) or 0) % 360
    if rotation in (90, 270):
        width, height = height, width
    return round(width, 1), round(height, 1)


def _page_content_bytes(page) -> bytes:
    try:
        contents = page.get_contents()
        if contents is None:
            return b''
        data = contents.get_data()
        return data if isinstance(data, bytes) else bytes(data or b'')
    except Exception:
        return b''


def _resolve_pdf_object(value):
    try:
        return value.get_object()
    except Exception:
        return value


def _resources_have_raster_image(resources, seen: set | None = None) -> bool:
    resources = _resolve_pdf_object(resources)
    if not hasattr(resources, 'get'):
        return False
    seen = seen or set()
    xobjects = _resolve_pdf_object(resources.get('/XObject'))
    if not hasattr(xobjects, 'values'):
        return False
    for reference in xobjects.values():
        obj = _resolve_pdf_object(reference)
        marker = (
            getattr(reference, 'idnum', None),
            getattr(reference, 'generation', None),
        )
        if marker != (None, None):
            if marker in seen:
                continue
            seen.add(marker)
        if not hasattr(obj, 'get'):
            continue
        subtype = str(obj.get('/Subtype', ''))
        if subtype == '/Image':
            return True
        if subtype == '/Form' and _resources_have_raster_image(
            obj.get('/Resources'),
            seen,
        ):
            return True
    return False


def _page_has_raster_image(page) -> bool:
    if _resources_have_raster_image(page.get('/Resources')):
        return True
    return bool(_re.search(rb'(?<!\w)BI(?!\w)', _page_content_bytes(page)))


def validate_compressed_pdf(original_path: str, compressed_path: str) -> list:
    """
    Valida que o PDF comprimido:
      - existe e não está vazio
      - e legivel por PdfReader
      - tem exatamente o mesmo numero de paginas que o original
      - preserva o tamanho visual de cada pagina

    Retorna lista de strings de warning (lista vazia = tudo OK).
    Nenhum dado sensivel (UUID, path, usuario) e incluido nas strings.
    """
    result: list = []
    if not os.path.exists(compressed_path) or os.path.getsize(compressed_path) == 0:
        result.append('compressed_missing_or_empty')
        return result
    if not validate_pdf_readable(compressed_path):
        result.append('compressed_unreadable')
        return result
    orig_n = count_pdf_pages(original_path)
    comp_n = count_pdf_pages(compressed_path)
    if comp_n != orig_n:
        result.append(f'page_count_mismatch:before={orig_n}:after={comp_n}')
        return result
    try:
        with open(original_path, 'rb') as original_handle, open(
            compressed_path,
            'rb',
        ) as compressed_handle:
            original_reader = PdfReader(original_handle)
            compressed_reader = PdfReader(compressed_handle)
            for page_number, (before, after) in enumerate(
                zip(original_reader.pages, compressed_reader.pages),
                start=1,
            ):
                before_size = _visual_page_size(before)
                after_size = _visual_page_size(after)
                if (
                    abs(before_size[0] - after_size[0]) > 1.0
                    or abs(before_size[1] - after_size[1]) > 1.0
                ):
                    result.append(f'page_layout_mismatch:page={page_number}')
    except Exception:
        result.append('compressed_structure_unreadable')
    return result


# ── Parâmetros GS por faixa de quality ───────────────────────────────────────
# Piso de DPI seguro — evita que páginas individuais sejam destruídas
# visualmente por downsampling excessivo.
def _validate_suspicious_compression(original_path: str, compressed_path: str) -> list[str]:
    warnings = validate_compressed_pdf(original_path, compressed_path)
    if warnings:
        return warnings
    try:
        with open(original_path, 'rb') as before_handle, open(
            compressed_path, 'rb'
        ) as after_handle:
            before_reader = PdfReader(before_handle)
            after_reader = PdfReader(after_handle)
            pairs = zip(before_reader.pages, after_reader.pages)
            for page_number, (before, after) in enumerate(pairs, start=1):
                if _page_content_bytes(before).strip() and not _page_content_bytes(after).strip():
                    warnings.append(f'page_content_missing:page={page_number}')
                    continue
                before_text = ''.join((before.extract_text() or '').split())
                after_text = ''.join((after.extract_text() or '').split())
                if (
                    len(before_text) >= 8
                    and len(after_text) < max(4, int(len(before_text) * 0.5))
                ):
                    warnings.append(f'page_text_missing:page={page_number}')
                if _page_has_raster_image(before) and not _page_has_raster_image(after):
                    warnings.append(f'page_image_missing:page={page_number}')
    except Exception:
        warnings.append('suspicious_structure_unreadable')
    return warnings


MIN_SAFE_DPI = 72

# Limiares de tamanho suspeito. Eles acionam validação estrutural reforçada;
# tamanho isolado nunca basta para rejeitar uma compressão legítima.
MIN_GROUP_SIZE_KB    = 10     # KB absolutos
MIN_GROUP_SIZE_RATIO = 0.05   # 5% do original — abaixo disso exige inspeção
MIN_COMPRESSION_GAIN_RATIO = 0.01  # abaixo de 1% o baseline é mais honesto
GS_CAPTURE_LIMIT_CHARS = 64 * 1024
GS_SANDBOX_MEMORY_MB = int(os.environ.get("GS_SANDBOX_MEMORY_MB", "768"))
GS_SANDBOX_FILE_MB = int(os.environ.get("GS_SANDBOX_FILE_MB", "512"))
GS_SANDBOX_MAX_PROCESSES = int(os.environ.get("GS_SANDBOX_MAX_PROCESSES", "16"))
MAX_TARGET_COMPRESSION_ATTEMPTS = 3
TARGET_SIZE_PROFILES = (
    {"slug": "conservador", "quality": 90, "dpi": 200},
    {"slug": "alto", "quality": 80, "dpi": 160},
    {"slug": "equilibrado", "quality": 70, "dpi": 130},
    {"slug": "medio", "quality": 55, "dpi": 110},
    {"slug": "forte", "quality": 45, "dpi": 100},
    {"slug": "agressivo", "quality": 35, "dpi": 85},
    {"slug": "maximo_seguro", "quality": 25, "dpi": 72},
)
TARGET_JPEG_RECOMPRESSION_PROFILE = {
    "slug": "recompressao_jpeg_agressiva",
    "quality": 25,
    "dpi": 72,
    "force_jpeg_recompression": True,
}
TARGET_GRAYSCALE_PROFILE = {
    "slug": "tons_de_cinza",
    "quality": 25,
    "dpi": 72,
    "force_jpeg_recompression": True,
    "convert_to_grayscale": True,
}


@dataclass(frozen=True)
class GhostscriptExecution:
    """Resultado sanitizado e reutilizável do executor Ghostscript."""

    usable: bool
    fallback_reason: str | None
    input_size: int
    output_size: int
    expected_pages: int
    actual_pages: int
    returncode: int | None


class CompressionGroupWarnings(list):
    """Lista compatível com o contrato antigo, com metadados do fallback."""

    def __init__(self, values=(), *, fallback_reason: str | None = None):
        super().__init__(values)
        self.fallback_reason = fallback_reason

    @property
    def used_original(self) -> bool:
        return self.fallback_reason is not None


def _build_gs_image_params(quality: int, dpi: int) -> dict:
    """
    Mapeia (quality, dpi) → parâmetros reais do Ghostscript.

    Separação entre perfis (mesmo dpi=100):
      quality=80 → qfactor=0.447, color_res=100, 4:4:4, Bicubic
      quality=50 → qfactor=0.707, color_res= 85, 4:2:2, Average
      quality=20 → qfactor=0.894, color_res= 72, 4:2:0, Subsample

    QFactor no GS fica entre 0.0 e 1.0; menor preserva mais qualidade.
    A curva evita saturar os perfis baixos; DPI e chroma subsampling reforçam
    a separação entre níveis de agressividade.
    HSamples/VSamples:
      [1 1 1 1] = 4:4:4 — preserva crominância
      [2 1 1 1] = 4:2:2 — reduz crominância horizontal (perda leve)
      [2 1 1 2] = 4:2:0 — reduz h+v (máxima compressão, perda visível)
    """
    q = max(20, min(100, quality))

    # Escala Adobe/GS documentada: float entre 0.0 e 1.0.
    qfactor = round((1.0 - q / 100.0) ** 0.5, 3)

    # ── Resolução efetiva: quality E dpi combinados ────────────────────────
    # quality baixa reduz a resolução além do que o dpi pede sozinho.
    # q≥75 → preserva; q≥50 → -15%; q≥35 → -30%; q<35 → -45%
    if q >= 75:
        res_factor = 1.00
    elif q >= 50:
        res_factor = 0.85
    elif q >= 35:
        res_factor = 0.70
    else:
        res_factor = 0.55

    color_res = max(MIN_SAFE_DPI, int(dpi * res_factor))
    gray_res  = color_res
    # Mono sofre menos — fator ligeiramente mais alto, mesmo piso
    mono_res  = max(MIN_SAFE_DPI, int(dpi * min(1.0, res_factor * 1.2)))

    # ── Chroma subsampling ─────────────────────────────────────────────────
    if q >= 75:
        hsamples = '[1 1 1 1]'    # 4:4:4
        vsamples = '[1 1 1 1]'
    elif q >= 45:
        hsamples = '[2 1 1 1]'    # 4:2:2
        vsamples = '[1 1 1 1]'
    else:
        hsamples = '[2 1 1 2]'    # 4:2:0
        vsamples = '[2 1 1 2]'

    # ── Algoritmo de downsample ────────────────────────────────────────────
    if q >= 75:
        downsample = 'Bicubic'
    elif q >= 45:
        downsample = 'Average'
    else:
        downsample = 'Subsample'

    return {
        'jpeg_q':     q,
        'qfactor':    round(qfactor, 4),
        'color_res':  color_res,
        'gray_res':   gray_res,
        'mono_res':   mono_res,
        'downsample': downsample,
        'hsamples':   hsamples,
        'vsamples':   vsamples,
    }


def _build_gs_args(
    input_pdf: str,
    output_pdf: str,
    params: dict,
    *,
    force_jpeg_recompression: bool = False,
    convert_to_grayscale: bool = False,
) -> list:
    """
    Monta args do GS usando setdistillerparams via -c (PostScript inline).
    hsamples/vsamples controlam chroma subsampling por perfil de quality.
    Sem -dPDFSETTINGS para evitar conflito com parâmetros explícitos.
    """
    gs_cmd = _get_gs_cmd()
    qf  = params['qfactor']
    ds  = params['downsample']
    cr  = params['color_res']
    gr  = params['gray_res']
    mr  = params['mono_res']
    hs  = params['hsamples']
    vs  = params['vsamples']

    distiller_ps = (
        f'<< '
        f'/ColorImageDict << /QFactor {qf} /Blend 1 /ColorTransform 1 '
        f'/HSamples {hs} /VSamples {vs} >> '
        f'/GrayImageDict  << /QFactor {qf} /Blend 1 /ColorTransform 1 '
        f'/HSamples {hs} /VSamples {vs} >> '
        f'/ColorImageResolution {cr} '
        f'/GrayImageResolution  {gr} '
        f'/MonoImageResolution  {mr} '
        f'/DownsampleColorImages true '
        f'/DownsampleGrayImages  true '
        f'/DownsampleMonoImages  true '
        f'/ColorImageDownsampleType  /{ds} '
        f'/GrayImageDownsampleType   /{ds} '
        f'/MonoImageDownsampleType   /Subsample '
        f'/ColorImageDownsampleThreshold 1.0 '
        f'/GrayImageDownsampleThreshold  1.0 '
        f'/MonoImageDownsampleThreshold  1.0 '
        f'/AutoFilterColorImages false '
        f'/AutoFilterGrayImages  false '
        f'/EncodeColorImages true '
        f'/EncodeGrayImages  true '
        f'/ColorImageFilter /DCTEncode '
        f'/GrayImageFilter  /DCTEncode '
        f'/CompressPages true '
        f'/EmbedAllFonts  true '
        f'/SubsetFonts    true '
        f'>> setdistillerparams'
    )

    args = [
        gs_cmd,
        '-sDEVICE=pdfwrite',
        '-dCompatibilityLevel=1.6',
        '-dSAFER',
        '-dNOPAUSE',
        '-dBATCH',
        '-dShowAnnots=true',
    ]
    if force_jpeg_recompression:
        args.extend(
            [
                '-dPassThroughJPEGImages=false',
                '-dPassThroughJPXImages=false',
            ]
        )
    if convert_to_grayscale:
        args.append('-sColorConversionStrategy=Gray')
    args.extend(
        [
            f'-sOutputFile={output_pdf}',
            '-c', distiller_ps,
            '-f', input_pdf,
        ]
    )
    return args


def _minimal_ghostscript_env() -> dict[str, str]:
    """Mantém apenas variáveis necessárias ao runtime, sem copiar segredos."""
    allowed = (
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "GS_LIB",
        "GS_FONTPATH",
    )
    return {name: os.environ[name] for name in allowed if os.environ.get(name)}


def _discard_ghostscript_output(output_pdf: str) -> None:
    try:
        os.remove(output_pdf)
    except OSError:
        pass


def run_ghostscript_command(
    args: list[str],
    *,
    working_parent: str,
    timeout: int,
) -> subprocess.CompletedProcess:
    """Único ponto de criação do processo Ghostscript no fluxo /compress."""
    with tempfile.TemporaryDirectory(
        prefix="gs_exec_",
        dir=working_parent,
    ) as workdir:
        return run_in_sandbox(
            args,
            cwd=workdir,
            timeout=timeout,
            cpu_seconds=timeout,
            mem_mb=GS_SANDBOX_MEMORY_MB,
            file_mb=GS_SANDBOX_FILE_MB,
            max_processes=GS_SANDBOX_MAX_PROCESSES,
            env=_minimal_ghostscript_env(),
            output_limit_chars=GS_CAPTURE_LIMIT_CHARS,
        )


def execute_ghostscript_validated(
    input_pdf: str,
    output_pdf: str,
    quality: int,
    dpi: int,
    *,
    expected_pages: int | None = None,
    force_jpeg_recompression: bool = False,
    convert_to_grayscale: bool = False,
    timeout_seconds: int | float | None = None,
) -> GhostscriptExecution:
    """
    Executor único do Ghostscript para compressão real e futura calibração.

    O sandbox limita recursos em Linux e mantém timeout/captura limitada nas
    demais plataformas. Ele não oferece isolamento de rede.
    """
    input_pdf = os.path.abspath(input_pdf)
    output_pdf = os.path.abspath(output_pdf)
    params  = _build_gs_image_params(quality, dpi)
    gs_args = _build_gs_args(
        input_pdf,
        output_pdf,
        params,
        force_jpeg_recompression=force_jpeg_recompression,
        convert_to_grayscale=convert_to_grayscale,
    )
    effective_timeout = (
        GHOSTSCRIPT_TIMEOUT
        if timeout_seconds is None
        else max(1, min(GHOSTSCRIPT_TIMEOUT, int(timeout_seconds)))
    )
    input_size = os.path.getsize(input_pdf) if os.path.exists(input_pdf) else 0
    expected = expected_pages if expected_pages is not None else count_pdf_pages(input_pdf)

    current_app.logger.info(
        '[compress-gs] quality=%d dpi=%d jpeg_recompress=%s '
        '→ jpeg_q=%d qfactor=%.4f '
        'color_res=%d gray_res=%d mono_res=%d downsample=%s '
        'hsamples=%s vsamples=%s',
        quality, dpi, force_jpeg_recompression,
        params['jpeg_q'], params['qfactor'],
        params['color_res'], params['gray_res'], params['mono_res'],
        params['downsample'], params['hsamples'], params['vsamples'],
    )
    if convert_to_grayscale:
        current_app.logger.info('[compress-gs] conversao_cores=gray')
    # Full command logged at INFO for cross-platform comparison (Windows vs Linux).
    # Upload paths are masked so UUIDs don't appear in logs; flags/params are intact.
    current_app.logger.info('[compress-gs-cmd] %s', _fmt_gs_cmd(gs_args))

    _discard_ghostscript_output(output_pdf)
    returncode = None
    try:
        output_dir = os.path.dirname(os.path.abspath(output_pdf))
        result = run_ghostscript_command(
            gs_args,
            working_parent=output_dir,
            timeout=effective_timeout,
        )
        returncode = result.returncode
        if result.returncode != 0:
            current_app.logger.error(
                '[compress-gs] falhou returncode=%d stdout_chars=%d stderr_chars=%d',
                result.returncode,
                len(result.stdout or ''),
                len(result.stderr or ''),
            )
            reason = "gs_error"
        else:
            reason = None
            current_app.logger.debug(
                '[compress-gs] OK stderr_chars=%d', len(result.stderr or '')
            )
    except subprocess.TimeoutExpired:
        current_app.logger.error('[compress-gs] timeout (%ds)', effective_timeout)
        reason = "timeout"
    except FileNotFoundError:
        current_app.logger.error('[compress-gs] não encontrado')
        reason = "not_found"
    except OSError as exc:
        current_app.logger.error(
            '[compress-gs] falha de execução: %s', type(exc).__name__
        )
        reason = "execution_error"
    except Exception as exc:
        current_app.logger.error(
            '[compress-gs] falha inesperada controlada: %s',
            type(exc).__name__,
        )
        reason = "execution_error"

    output_size = os.path.getsize(output_pdf) if os.path.exists(output_pdf) else 0
    actual_pages = count_pdf_pages(output_pdf) if output_size else 0

    if reason is None and output_size == 0:
        reason = "output_missing"
    if reason is None and not validate_pdf_readable(output_pdf):
        reason = "output_unreadable"
    if reason is None and actual_pages != expected:
        reason = "page_count_mismatch"
    suspicious_size = (
        output_size < MIN_GROUP_SIZE_KB * 1024
        or (input_size > 0 and output_size / input_size < MIN_GROUP_SIZE_RATIO)
    )
    if reason is None and suspicious_size:
        structural_warnings = _validate_suspicious_compression(
            input_pdf,
            output_pdf,
        )
        if structural_warnings:
            reason = 'suspicious_content_mismatch'
            current_app.logger.warning(
                '[compress-gs] saida pequena rejeitada por estrutura warnings=%s',
                ','.join(structural_warnings),
            )
        else:
            current_app.logger.info(
                '[compress-gs] saida pequena validada por estrutura ratio=%.4f',
                output_size / input_size if input_size else 0.0,
            )
    if reason is None and output_size >= input_size:
        reason = "gs_larger"
    if (
        reason is None
        and input_size > 0
        and (1 - output_size / input_size) < MIN_COMPRESSION_GAIN_RATIO
    ):
        reason = "insufficient_gain"

    if reason is not None:
        _discard_ghostscript_output(output_pdf)

    return GhostscriptExecution(
        usable=reason is None,
        fallback_reason=reason,
        input_size=input_size,
        output_size=output_size,
        expected_pages=expected,
        actual_pages=actual_pages,
        returncode=returncode,
    )


def _run_ghostscript(input_pdf: str, output_pdf: str, quality: int, dpi: int) -> None:
    """Wrapper compatível; toda execução passa pelo executor validado."""
    result = execute_ghostscript_validated(
        input_pdf,
        output_pdf,
        quality,
        dpi,
    )
    if not result.usable:
        raise RuntimeError(f"ghostscript_output_rejected:{result.fallback_reason}")


# ── qpdf ──────────────────────────────────────────────────────────────────────
_QPDF_WARNING_LOGGED = False


def _qpdf_flatten(src: str, dst: str) -> None:
    global _QPDF_WARNING_LOGGED
    qpdf = _get_qpdf_cmd()
    if not qpdf:
        if not _QPDF_WARNING_LOGGED:
            current_app.logger.warning(
                'qpdf não encontrado — flatten desativado. '
                'Instale qpdf para melhor compatibilidade de anotações.'
            )
            _QPDF_WARNING_LOGGED = True
        shutil.copyfile(src, dst)
        return
    try:
        subprocess.run(
            [qpdf, '--silent', '--flatten-annotations=all',
             '--object-streams=generate', '--stream-data=compress', src, dst],
            check=True, capture_output=True, text=True, timeout=QPDF_TIMEOUT,
        )
    except Exception as e:
        current_app.logger.warning('[compress] qpdf flatten falhou: %s — copiando original', type(e).__name__)
        shutil.copyfile(src, dst)


def _qpdf_optimize_lossless(src: str, dst: str) -> str | None:
    qpdf = _get_qpdf_cmd()
    if not qpdf:
        shutil.copyfile(src, dst)
        return 'qpdf_unavailable'
    try:
        subprocess.run(
            [qpdf, '--silent', '--object-streams=generate',
             '--stream-data=compress', '--compress-streams=y', src, dst],
            check=True, capture_output=True, text=True, timeout=QPDF_TIMEOUT,
        )
        return None
    except Exception as e:
        current_app.logger.warning('[compress] qpdf lossless falhou: %s — copiando original', type(e).__name__)
        shutil.copyfile(src, dst)
        return 'qpdf_error'


# ── Rotações com pikepdf ──────────────────────────────────────────────────────
def _apply_rotations_pikepdf(src_pdf: str, pages, rotations, out_pdf: str) -> None:
    """pages=None → todas; pages=[] → guard explícito (vazio != None)"""
    if pages is not None and len(pages) == 0:
        shutil.copyfile(src_pdf, out_pdf)
        return
    try:
        import pikepdf  # noqa: PLC0415
    except ImportError:
        current_app.logger.warning('[compress] pikepdf não disponível — rotações ignoradas')
        shutil.copyfile(src_pdf, out_pdf)
        return

    rot = {int(k): int(v) for k, v in (rotations or {}).items()}
    with pikepdf.open(src_pdf) as pdf:
        total = len(pdf.pages)
        order = pages if pages is not None else list(range(1, total + 1))
        out   = pikepdf.Pdf.new()
        for pn in order:
            idx = pn - 1
            if idx < 0 or idx >= total:
                continue
            page = pdf.pages[idx]
            if pn in rot:
                current_r       = int(page.get('/Rotate', 0))
                page['/Rotate'] = (current_r + rot[pn]) % 360
            out.pages.append(page)
        out.save(out_pdf)


def _clear_page_rotations(src_pdf: str, out_pdf: str) -> dict[int, int]:
    '''Remove /Rotate antes do GS e devolve as rotações locais encontradas.'''
    import pikepdf  # noqa: PLC0415

    rotations: dict[int, int] = {}
    with pikepdf.open(src_pdf) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                rotation = int(page.get('/Rotate', 0) or 0) % 360
            except Exception:
                rotation = 0
            if rotation:
                rotations[page_number] = rotation
                try:
                    del page['/Rotate']
                except Exception:
                    page['/Rotate'] = 0
        pdf.save(out_pdf)
    return rotations


def _combined_local_rotations(
    base_rotations: dict[int, int],
    requested_rotations: dict | None,
) -> dict[int, int]:
    page_numbers = set(base_rotations) | set(requested_rotations or {})
    combined = {}
    for page_number in page_numbers:
        value = (
            int(base_rotations.get(page_number, 0))
            + int((requested_rotations or {}).get(page_number, 0))
        ) % 360
        if value:
            combined[page_number] = value
    return combined


A4_WIDTH_POINTS = 595.2756
A4_HEIGHT_POINTS = 841.8898


def resize_pdf_pages_to_a4(
    src_pdf: str,
    out_pdf: str,
    pages: list[int] | set[int] | tuple[int, ...],
) -> None:
    '''Ajusta páginas ao A4 sem distorção, centralizando o conteúdo.'''
    import pikepdf  # noqa: PLC0415

    selected = {int(page_number) for page_number in pages}
    if not selected:
        shutil.copyfile(src_pdf, out_pdf)
        return
    with pikepdf.open(src_pdf) as source, pikepdf.Pdf.new() as output:
        for page_number, page in enumerate(source.pages, start=1):
            if page_number not in selected:
                output.pages.append(page)
                continue
            box = page.trimbox
            source_width = float(box[2] - box[0])
            source_height = float(box[3] - box[1])
            if source_width <= 0 or source_height <= 0:
                raise ValueError('invalid_page_box_for_a4')
            if source_width > source_height:
                target_width, target_height = A4_HEIGHT_POINTS, A4_WIDTH_POINTS
            else:
                target_width, target_height = A4_WIDTH_POINTS, A4_HEIGHT_POINTS
            scale = min(
                target_width / source_width,
                target_height / source_height,
            )
            placed_width = source_width * scale
            placed_height = source_height * scale
            left = (target_width - placed_width) / 2
            bottom = (target_height - placed_height) / 2
            destination = output.add_blank_page(
                page_size=(target_width, target_height)
            )
            destination.add_overlay(
                page,
                pikepdf.Rectangle(
                    left,
                    bottom,
                    left + placed_width,
                    bottom + placed_height,
                ),
                expand=False,
            )
        output.save(out_pdf)


def build_selected_baseline(
    input_path: str,
    output_path: str,
    pages: list[int],
    *,
    rotations: dict | None = None,
    resize_pages: list[int] | set[int] | None = None,
    preserve_interactive: bool = False,
) -> None:
    """Monta o resultado correto sem Ghostscript para a seleção solicitada."""
    if preserve_interactive:
        write_preserving_pdf_subset(
            input_path,
            output_path,
            pages=pages,
            rotations=rotations,
        )
    else:
        parent = os.path.dirname(output_path)
        selected_path = os.path.join(parent, f'baseline_selected_{uuid.uuid4().hex}.pdf')
        unrotated_path = os.path.join(parent, f'baseline_unrotated_{uuid.uuid4().hex}.pdf')
        resized_path = os.path.join(parent, f'baseline_a4_{uuid.uuid4().hex}.pdf')
        try:
            _extract_pages(input_path, pages, selected_path)
            existing_rotations = _clear_page_rotations(
                selected_path,
                unrotated_path,
            )
            resize_set = {int(page_number) for page_number in (resize_pages or [])}
            resize_positions = {
                position
                for position, original_page in enumerate(pages, start=1)
                if original_page in resize_set
            }
            prepared_path = unrotated_path
            if resize_positions:
                resize_pdf_pages_to_a4(
                    unrotated_path,
                    resized_path,
                    resize_positions,
                )
                prepared_path = resized_path

            normalized_requested = {
                int(key): int(value)
                for key, value in (rotations or {}).items()
            }
            local_requested = {
                position: normalized_requested[original_page]
                for position, original_page in enumerate(pages, start=1)
                if original_page in normalized_requested
            }
            final_rotations = _combined_local_rotations(
                existing_rotations,
                local_requested,
            )
            _apply_rotations_pikepdf(
                prepared_path,
                list(range(1, len(pages) + 1)),
                final_rotations,
                output_path,
            )
        finally:
            _cleanup_paths((selected_path, unrotated_path, resized_path))

    if not validate_pdf_readable(output_path):
        _cleanup_paths((output_path,))
        raise RuntimeError("selected_baseline_unreadable")
    if count_pdf_pages(output_path) != len(pages):
        _cleanup_paths((output_path,))
        raise RuntimeError("selected_baseline_page_count_mismatch")


# ── Extração de páginas ───────────────────────────────────────────────────────
def _extract_pages(src_pdf: str, pages: list, out_pdf: str) -> None:
    with open(src_pdf, 'rb') as f:
        reader = PdfReader(f)
        writer = PdfWriter()
        total  = len(reader.pages)
        for pn in pages:
            idx = pn - 1
            if 0 <= idx < total:
                writer.add_page(reader.pages[idx])
        with open(out_pdf, 'wb') as fo:
            writer.write(fo)


# ── Análise enriquecida por página ───────────────────────────────────────────
def enrich_page_analysis(pages: list) -> list:
    """
    Enriquece a lista de páginas retornada pelo analyze com:
      - size_factor: quanto essa página é maior/menor que a média
      - quality_suggested: qualidade sugerida baseada no size_factor
      - dpi_suggested: DPI sugerido baseado no size_factor
      - resize_to_a4_suggested: false por padrão; o usuário pode ativar A4

    Portado conceitualmente do pdfAnalyzer.js (projeto de referência):
      - isLarge: área > 30% maior que a média
      - sizeFactor: area / avgArea
      - quality/dpi auto-ajustados proporcionalmente ao sizeFactor

    Não altera os valores definidos pelo usuário — apenas sugere defaults
    mais inteligentes para o frontend montar os cards.
    """
    if not pages:
        return pages

    # Calcular área média
    areas = [p.get('width', 595) * p.get('height', 842) for p in pages]
    avg_area = sum(areas) / len(areas) if areas else 1

    enriched = []
    for i, page in enumerate(pages):
        p = dict(page)  # cópia — não muta o original
        area = areas[i]
        size_factor = area / avg_area if avg_area else 1.0

        # Página "grande" se área > 30% acima da média (espelha pdfAnalyzer.js)
        is_large = size_factor > 1.3

        # Quality e DPI sugeridos — degradam proporcionalmente ao tamanho
        # Para páginas normais (factor≈1): quality=80, dpi=100
        # Para páginas 2× maiores: quality≈40, dpi≈50 (mesmos caps do pdfAnalyzer)
        if is_large:
            quality_suggested = max(20, round(80 / size_factor))
            dpi_suggested     = max(MIN_SAFE_DPI, round(100 / size_factor))
            resize_suggested  = False
        else:
            quality_suggested = 80
            dpi_suggested     = 100
            resize_suggested  = False

        p['size_factor']            = round(size_factor, 2)
        p['quality_suggested']      = quality_suggested
        p['dpi_suggested']          = dpi_suggested
        p['resize_to_a4_suggested'] = resize_suggested
        # is_large pode já vir do analyze original — sobrescrever com cálculo coerente
        p['is_large']               = is_large

        # Sobrescreve quality/dpi com os valores sugeridos — estes são os campos
        # que o frontend lê para montar os cards e enviar no payload de compressão.
        # Os campos _suggested são mantidos como referência, mas quality/dpi devem
        # refletir o default inteligente calculado aqui, não o placeholder fixo 80/100.
        p['quality']      = quality_suggested
        p['dpi']          = dpi_suggested
        p['resize_to_a4'] = False

        enriched.append(p)

    return enriched



def comprimir_pdf_com_params(
    input_path: str,
    output_path: str,
    pages: list,
    quality: int,
    dpi: int,
    resize_to_a4: bool = False,
    rotations: dict = None,
    force_jpeg_recompression: bool = False,
    convert_to_grayscale: bool = False,
    timeout_seconds: int | float | None = None,
) -> list:
    """
    Comprime um grupo de paginas do PDF de entrada.

    Retorna lista de warnings (strings). Lista vazia = sem problemas.
    Nunca entrega o PDF comprimido se ele tiver menos paginas que o grupo extraido.
    """
    warnings_out: list = []
    upload_folder = os.path.dirname(output_path)

    # Frente 1 — piso mínimo de DPI
    effective_dpi = dpi
    if dpi < MIN_SAFE_DPI:
        current_app.logger.warning(
            '[compress-group] dpi=%d abaixo do piso seguro (%d) — elevando para %d. n_pages=%d',
            dpi, MIN_SAFE_DPI, MIN_SAFE_DPI, len(pages),
        )
        effective_dpi = MIN_SAFE_DPI

    params = _build_gs_image_params(quality, effective_dpi)

    extracted_path = os.path.join(upload_folder, f'extracted_{uuid.uuid4().hex}.pdf')
    unrotated_path = os.path.join(upload_folder, f'unrotated_{uuid.uuid4().hex}.pdf')
    resized_path = os.path.join(upload_folder, f'a4_{uuid.uuid4().hex}.pdf')
    gs_output_path = os.path.join(upload_folder, f'gs_group_{uuid.uuid4().hex}.pdf')

    fallback_reason = None
    try:
        # Extrai apenas as páginas do grupo → extracted_path tem [1..n] páginas.
        _extract_pages(input_path, pages, extracted_path)
        existing_rotations = _clear_page_rotations(
            extracted_path,
            unrotated_path,
        )
        prepared_path = unrotated_path
        if resize_to_a4:
            resize_pdf_pages_to_a4(
                unrotated_path,
                resized_path,
                list(range(1, len(pages) + 1)),
            )
            prepared_path = resized_path

        # O PDF extraído usa numeração local; rotações precisam ser remapeadas.
        n_extracted = len(pages)
        remapped_pages = list(range(1, n_extracted + 1))
        page_remap = {
            orig: new for new, orig in zip(remapped_pages, pages)
        }
        remapped_rotations = (
            {
                page_remap[pn]: deg
                for pn, deg in rotations.items()
                if pn in page_remap
            }
            if rotations
            else None
        )
        final_rotations = _combined_local_rotations(
            existing_rotations,
            remapped_rotations,
        )

        size_in = os.path.getsize(prepared_path)
        current_app.logger.info(
            '[compress-group] n_pages=%d size_in=%.1f KB '
            'quality=%d dpi_req=%d dpi_eff=%d -> jpeg_q=%d qfactor=%.4f '
            'color_res=%d gray_res=%d downsample=%s hsamples=%s vsamples=%s',
            len(pages), size_in / 1024,
            quality, dpi, effective_dpi,
            params['jpeg_q'], params['qfactor'],
            params['color_res'], params['gray_res'], params['downsample'],
            params['hsamples'], params['vsamples'],
        )

        executor_kwargs = {
            "quality": quality,
            "dpi": effective_dpi,
            "expected_pages": n_extracted,
        }
        if force_jpeg_recompression:
            executor_kwargs["force_jpeg_recompression"] = True
        if convert_to_grayscale:
            executor_kwargs["convert_to_grayscale"] = True
        if timeout_seconds is not None:
            executor_kwargs["timeout_seconds"] = timeout_seconds
        execution = execute_ghostscript_validated(
            prepared_path,
            gs_output_path,
            **executor_kwargs,
        )
        fallback_reason = execution.fallback_reason
        candidate_path = gs_output_path if execution.usable else prepared_path

        if execution.usable:
            reduction = (
                (1 - execution.output_size / execution.input_size) * 100
                if execution.input_size
                else 0
            )
            current_app.logger.info(
                '[compress-group] n_pages=%d size_before=%.1f KB '
                'size_after=%.1f KB reduction=%.1f%%',
                len(pages),
                execution.input_size / 1024,
                execution.output_size / 1024,
                reduction,
            )
        else:
            warnings_out.append(f'compression_fallback:{fallback_reason}')
            if fallback_reason == "page_count_mismatch":
                warnings_out.append(
                    'page_count_mismatch:'
                    f'before={execution.expected_pages}:after={execution.actual_pages}'
                )

            current_app.logger.warning(
                '[compress-group] fallback=group_original n_pages=%d reason=%s',
                len(pages), fallback_reason,
            )
        if final_rotations:
            _apply_rotations_pikepdf(
                candidate_path,
                remapped_pages,
                final_rotations,
                output_path,
            )
        else:
            shutil.copyfile(candidate_path, output_path)

    finally:
        for p in (
            extracted_path,
            unrotated_path,
            resized_path,
            gs_output_path,
        ):
            try:
                os.remove(p)
            except OSError:
                pass

    return CompressionGroupWarnings(
        warnings_out,
        fallback_reason=fallback_reason,
    )


# ── comprimir_pdf (rota legada) ───────────────────────────────────────────────
PROFILES = {
    'leve':       {'quality': 85, 'dpi': 150},
    'equilibrio': {'quality': 72, 'dpi': 120},
    'forte':      {'quality': 45, 'dpi': 96},
    'lossless':   {'quality': 95, 'dpi': 300},
}

_PROFILE_ALIASES = {
    'light':    'leve',
    'balanced': 'equilibrio',
    'strong':   'forte',
    'max':      'forte',
}

# ── Aliases de compatibilidade pública ────────────────────────────────────────
# compress.py e qualquer outro módulo que importe esses nomes continuam funcionando
# sem precisar ser alterados.

# USER_PROFILES: mapa público usado pela rota para validação e listagem de perfis
USER_PROFILES = PROFILES

# _get_ghostscript_cmd: nome antigo — aponta para a função atual
_get_ghostscript_cmd = _get_gs_cmd


def comprimir_pdf(
    file,
    pages=None,
    rotations=None,
    modificacoes=None,
    profile: str = 'equilibrio',
) -> tuple:
    """
    Comprime um PDF usando o perfil especificado.

    Retorna: (output_path: str, warnings: list)
    warnings e lista de strings descritivas sem dados sensiveis.
    """
    warnings_out: list = []
    internal_profile = _PROFILE_ALIASES.get(profile, profile)
    if internal_profile not in PROFILES and internal_profile != 'lossless':
        internal_profile = 'equilibrio'

    upload_folder  = current_app.config.get('UPLOAD_FOLDER', '/tmp')
    cleanup        = []
    basename       = uuid.uuid4().hex
    input_path     = os.path.join(upload_folder, f'upload_{basename}.pdf')

    file.save(input_path)
    cleanup.append(input_path)
    original_size  = os.path.getsize(input_path)
    original_pages = _page_count(input_path)

    current_app.logger.info(
        '[compress] start profile=%s pages=%d size_before=%.1f KB',
        internal_profile, original_pages, original_size / 1024,
    )

    # Sanitize
    sanitized_path = os.path.join(upload_folder, f'sanitized_{basename}.pdf')
    if _HAS_SANITIZE:
        try:
            sanitize_pdf_preserving_content(input_path, sanitized_path)
            cleanup.append(sanitized_path)
        except Exception as e:
            current_app.logger.warning('[compress] sanitize falhou: %s', type(e).__name__)
            _cleanup_paths(cleanup)
            raise RuntimeError('sanitize_failed') from e
    else:
        current_app.logger.error('[compress] sanitize indisponivel')
        _cleanup_paths(cleanup)
        raise RuntimeError('sanitize_unavailable')

    try:
        preservation = pdf_requires_content_preservation(sanitized_path)
    except Exception as e:
        current_app.logger.warning('[compress] inspeccao de preservacao falhou: %s', type(e).__name__)
        _cleanup_paths(cleanup)
        raise RuntimeError('preservation_inspection_failed') from e

    if preservation.get("requires_preservation"):
        out_path = os.path.join(
            upload_folder,
            f'preserved_{basename}_{uuid.uuid4().hex}.pdf',
        )
        write_preserving_pdf_subset(
            sanitized_path,
            out_path,
            pages=pages,
            rotations=rotations,
        )
        baseline_size = os.path.getsize(out_path)
        total_source_pages = count_pdf_pages(sanitized_path)
        selected_pages = (
            [
                int(page_number)
                for page_number in pages
                if 1 <= int(page_number) <= total_source_pages
            ]
            if pages is not None
            else list(range(1, total_source_pages + 1))
        )
        interactive_page_set = set(
            preservation.get('interactive_pages', [])
        )
        interactive_positions = [
            position
            for position, page_number in enumerate(
                selected_pages,
                start=1,
            )
            if page_number in interactive_page_set
        ]
        safe_positions = [
            position
            for position, page_number in enumerate(
                selected_pages,
                start=1,
            )
            if page_number not in interactive_page_set
        ]
        full_preservation = bool(
            preservation.get('requires_full_document_preservation')
        )
        can_compress_selectively = bool(
            safe_positions
            and not full_preservation
            and internal_profile != 'lossless'
        )
        warnings_out.extend(
            pdf_preservation_warnings(
                preservation,
                selective=bool(
                    can_compress_selectively and interactive_positions
                ),
            )
        )

        if can_compress_selectively:
            group_path = os.path.join(
                upload_folder,
                f'preserved_group_{basename}_{uuid.uuid4().hex}.pdf',
            )
            merged_path = os.path.join(
                upload_folder,
                f'comprimido_{basename}_{uuid.uuid4().hex}.pdf',
            )
            try:
                profile_params = PROFILES.get(
                    internal_profile,
                    PROFILES['equilibrio'],
                )
                group_warnings = comprimir_pdf_com_params(
                    input_path=out_path,
                    output_path=group_path,
                    pages=safe_positions,
                    quality=profile_params['quality'],
                    dpi=profile_params['dpi'],
                    resize_to_a4=False,
                    rotations=None,
                )
                warnings_out.extend(group_warnings)
                replacements = {
                    position: (group_path, source_index)
                    for source_index, position in enumerate(safe_positions)
                }
                replace_pdf_pages_preserving_catalog(
                    out_path,
                    merged_path,
                    replacements,
                )
                merged_size = os.path.getsize(merged_path)
                merged_warnings = validate_compressed_pdf(
                    out_path,
                    merged_path,
                )
                merged_gain = (
                    1 - merged_size / baseline_size
                    if baseline_size and merged_size
                    else 0.0
                )
                if (
                    not merged_warnings
                    and validate_pdf_readable(merged_path)
                    and merged_size < baseline_size
                    and merged_gain >= MIN_COMPRESSION_GAIN_RATIO
                ):
                    _cleanup_paths(cleanup + [out_path, group_path])
                    current_app.logger.info(
                        '[compress] result=partial_interactive_preservation '
                        'pages=%d compressed_pages=%d preserved_pages=%d '
                        'reduction=%.1f%%',
                        len(selected_pages),
                        len(safe_positions),
                        len(interactive_positions),
                        merged_gain * 100,
                    )
                    return merged_path, CompressionGroupWarnings(
                        warnings_out,
                        fallback_reason='partial_interactive_preservation',
                    )
                warnings_out.extend(merged_warnings)
            except Exception as exc:
                current_app.logger.warning(
                    '[compress] selective preservation failed: %s',
                    type(exc).__name__,
                )
                warnings_out.append(
                    'compression_fallback:selective_processing_failed'
                )
            finally:
                _cleanup_paths((group_path,))
            _cleanup_paths((merged_path,))

        size_after = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        current_app.logger.info(
            '[compress] modo_preservador pages=%d size_after=%.1f KB warnings=%d',
            count_pdf_pages(out_path), size_after / 1024, len(warnings_out),
        )
        _cleanup_paths(cleanup)
        return out_path, CompressionGroupWarnings(
            warnings_out,
            fallback_reason=(
                'selected_baseline'
                if can_compress_selectively
                else 'preserved_interactive'
            ),
        )

    # qpdf flatten
    flat_path = os.path.join(upload_folder, f'flat_{basename}.pdf')
    _qpdf_flatten(sanitized_path, flat_path)
    cleanup.append(flat_path)
    stage_source = flat_path

    # Lossless
    if internal_profile == 'lossless':
        out_path = os.path.join(upload_folder, f'comprimido_{basename}_{uuid.uuid4().hex}.pdf')
        lossless_candidate = out_path
        if rotations:
            lossless_candidate = os.path.join(upload_folder, f'lossless_{basename}.pdf')
            cleanup.append(lossless_candidate)
        lossless_fallback = _qpdf_optimize_lossless(stage_source, lossless_candidate)
        if rotations:
            _apply_rotations_pikepdf(
                lossless_candidate,
                None,
                rotations,
                out_path,
            )
        if lossless_fallback:
            warnings_out.append(f'compression_fallback:{lossless_fallback}')
        size_after = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        current_app.logger.info(
            '[compress] result=lossless size_after=%.1f KB reduction=%.1f%%',
            size_after / 1024,
            (1 - size_after / original_size) * 100 if original_size else 0,
        )
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass
        return out_path, CompressionGroupWarnings(
            warnings_out,
            fallback_reason=lossless_fallback,
        )

    # Ghostscript
    prof    = PROFILES.get(internal_profile, PROFILES['equilibrio'])
    quality = prof['quality']
    dpi     = prof['dpi']
    out_gs  = os.path.join(upload_folder, f'comprimido_{basename}_{uuid.uuid4().hex}.pdf')
    gs_candidate = out_gs
    if rotations:
        gs_candidate = os.path.join(upload_folder, f'gs_{basename}.pdf')
        cleanup.append(gs_candidate)
    fallback_reason = None

    try:
        _run_ghostscript(stage_source, gs_candidate, quality=quality, dpi=dpi)
        size_after  = os.path.getsize(gs_candidate)
        reduction   = (1 - size_after / original_size) * 100 if original_size else 0

        # ── Validação pós-GS: contagem de páginas ─────────────────────────
        page_warnings = validate_compressed_pdf(stage_source, gs_candidate)
        if page_warnings:
            current_app.logger.warning(
                '[compress] fallback=page_loss pages_original=%d warnings=%s — '
                'entregando PDF pre-GS para preservar conteudo',
                original_pages, page_warnings,
            )
            shutil.copyfile(stage_source, gs_candidate)
            fallback_reason = 'validation_failed'
            warnings_out.append('compression_fallback:validation_failed')
            warnings_out.extend(page_warnings)
        else:
            pages_after = count_pdf_pages(gs_candidate)
            current_app.logger.info(
                '[compress] gs done pages_before=%d pages_after=%d '
                'size_before=%.1f KB size_after=%.1f KB reduction=%.1f%%',
                original_pages, pages_after,
                original_size / 1024, size_after / 1024, reduction,
            )
            if size_after >= original_size:
                current_app.logger.info('[compress] fallback=gs_larger — entregando original')
                shutil.copyfile(stage_source, gs_candidate)
                fallback_reason = 'gs_larger'
                warnings_out.append('compression_fallback:gs_larger')

        if rotations:
            _apply_rotations_pikepdf(
                gs_candidate,
                None,
                rotations,
                out_gs,
            )
        return out_gs, CompressionGroupWarnings(
            warnings_out,
            fallback_reason=fallback_reason,
        )

    except Exception as exc:
        current_app.logger.error('[compress] GS falhou: %s', type(exc).__name__)
        prefix = 'ghostscript_output_rejected:'
        message = str(exc)
        fallback_reason = (
            message.removeprefix(prefix)
            if message.startswith(prefix)
            else 'execution_error'
        )
        warnings_out.append(f'compression_fallback:{fallback_reason}')
        shutil.copyfile(stage_source, gs_candidate)
        if rotations:
            _apply_rotations_pikepdf(
                gs_candidate,
                None,
                rotations,
                out_gs,
            )
        return out_gs, CompressionGroupWarnings(
            warnings_out,
            fallback_reason=fallback_reason,
        )

    finally:
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass
