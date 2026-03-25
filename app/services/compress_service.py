"""
compress_service.py — Grupo Vital PDFs

Nota sobre parâmetros do Ghostscript:
  Não usamos -dPDFSETTINGS porque ele define ColorACSImageDict internamente,
  podendo sobrescrever parâmetros externos dependendo da versão/build do GS.
  Parâmetros de imagem são passados via setdistillerparams (PostScript inline),
  que é a forma portável e confiável para controlar qualidade JPEG e resolução.

Nota sobre QFactor:
  O GS usa QFactor (0.0–1.0) no distiller, não JPEG Q (0–100).
  Mapeamento: QFactor = round(1.0 - jpeg_q / 100.0, 3)
  Q=88 → QF=0.120  Q=72 → QF=0.280  Q=45 → QF=0.550
  HSamples/VSamples [1 1 1 1] desativam chroma subsampling.

Nota sobre resolução efetiva:
  color_res = min(dpi, cap_da_faixa)
  Se dpi < cap → dpi domina. Se dpi > cap → cap domina.
  Exemplo: quality=80 (cap=200), dpi=100 → color_res=100 (não 200).
"""
import os
import shutil
import subprocess
import uuid

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter  # type: ignore

from flask import current_app

# ── Configuração ──────────────────────────────────────────────────────────────
GHOSTSCRIPT_TIMEOUT = int(os.environ.get('GS_TIMEOUT',   '120'))
QPDF_TIMEOUT        = int(os.environ.get('QPDF_TIMEOUT', '60'))

try:
    from app.services.sanitize_service import sanitize_pdf
    _HAS_SANITIZE = True
except ImportError:
    _HAS_SANITIZE = False

try:
    from app.utils.pdf_utils import page_count as _ext_page_count
    _HAS_PDF_UTILS = True
except ImportError:
    _HAS_PDF_UTILS = False


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_gs_cmd() -> str:
    # GS_PATH env var permite forçar o caminho exato em produção Linux
    # (útil quando gs está em /usr/local/bin e não está no PATH do processo systemd/Gunicorn).
    env_path = os.environ.get('GS_PATH', '').strip()
    if env_path and shutil.which(env_path):
        return env_path
    for candidate in ('gswin64c', 'gswin32c', 'gs'):
        if shutil.which(candidate):
            return candidate
    return 'gs'


def _get_qpdf_cmd():
    return shutil.which('qpdf')


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


# ── Parâmetros GS por faixa de quality ───────────────────────────────────────
# Piso de DPI seguro — evita que páginas individuais sejam destruídas
# visualmente por downsampling excessivo.
MIN_SAFE_DPI = 72

# Limiar de tamanho suspeito — se o grupo comprimido ficar abaixo desse
# valor absoluto OU desse percentual do original, aciona fallback de segurança.
MIN_GROUP_SIZE_KB    = 10     # KB absolutos
MIN_GROUP_SIZE_RATIO = 0.05   # 5% do original — abaixo disso é suspeito


def _build_gs_image_params(quality: int, dpi: int) -> dict:
    """
    Mapeia (quality, dpi) → parâmetros reais do Ghostscript.

    Separação entre perfis (mesmo dpi=100):
      quality=80 → qfactor=0.30, color_res=100, 4:4:4, Bicubic
      quality=50 → qfactor=0.75, color_res= 85, 4:2:2, Average
      quality=20 → qfactor=1.50, color_res= 72, 4:2:0, Subsample

    QFactor no GS: 0.0=melhor qualidade, valores >1.0=destrutivo (intencional para q baixa).
    HSamples/VSamples:
      [1 1 1 1] = 4:4:4 — preserva crominância
      [2 1 1 1] = 4:2:2 — reduz crominância horizontal (perda leve)
      [2 1 1 2] = 4:2:0 — reduz h+v (máxima compressão, perda visível)
    """
    q = max(20, min(100, quality))

    # ── QFactor: curva não-linear ──────────────────────────────────────────
    # q=100→0.05  q=80→0.30  q=60→0.60  q=40→1.00  q=20→1.50
    if q >= 80:
        qfactor = 0.05 + (100 - q) / 20.0 * 0.25     # 0.05 → 0.30
    elif q >= 60:
        qfactor = 0.30 + (80  - q) / 20.0 * 0.30     # 0.30 → 0.60
    elif q >= 40:
        qfactor = 0.60 + (60  - q) / 20.0 * 0.40     # 0.60 → 1.00
    else:
        qfactor = 1.00 + (40  - q) / 20.0 * 0.50     # 1.00 → 1.50

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


def _build_gs_args(input_pdf: str, output_pdf: str, params: dict) -> list:
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

    return [
        gs_cmd,
        '-sDEVICE=pdfwrite',
        '-dCompatibilityLevel=1.6',
        '-dSAFER',
        '-dNOPAUSE',
        '-dBATCH',
        '-dShowAnnots=true',
        f'-sOutputFile={output_pdf}',
        '-c', distiller_ps,
        '-f', input_pdf,
    ]


def _run_ghostscript(input_pdf: str, output_pdf: str, quality: int, dpi: int) -> None:
    params  = _build_gs_image_params(quality, dpi)
    gs_args = _build_gs_args(input_pdf, output_pdf, params)

    current_app.logger.info(
        '[compress-gs] quality=%d dpi=%d → jpeg_q=%d qfactor=%.4f '
        'color_res=%d gray_res=%d mono_res=%d downsample=%s '
        'hsamples=%s vsamples=%s',
        quality, dpi,
        params['jpeg_q'], params['qfactor'],
        params['color_res'], params['gray_res'], params['mono_res'],
        params['downsample'], params['hsamples'], params['vsamples'],
    )
    current_app.logger.debug('[compress-gs] cmd: %s', ' '.join(gs_args))

    try:
        result = subprocess.run(
            gs_args,
            check=False,
            capture_output=True,
            text=True,
            timeout=GHOSTSCRIPT_TIMEOUT,
        )
        if result.returncode != 0:
            current_app.logger.error(
                '[compress-gs] falhou returncode=%d\nSTDOUT:\n%s\nSTDERR:\n%s',
                result.returncode,
                result.stdout[-2000:] or '(vazio)',
                result.stderr[-2000:] or '(vazio)',
            )
            raise RuntimeError(f'Ghostscript retornou código {result.returncode}')
        current_app.logger.debug(
            '[compress-gs] OK stderr tail: %s', result.stderr[-300:] or '(vazio)'
        )
    except subprocess.TimeoutExpired:
        current_app.logger.error('[compress-gs] timeout (%ds)', GHOSTSCRIPT_TIMEOUT)
        raise RuntimeError('Ghostscript timeout')
    except FileNotFoundError:
        current_app.logger.error('[compress-gs] não encontrado: %s', _get_gs_cmd())
        raise RuntimeError(f'Ghostscript não encontrado: {_get_gs_cmd()}')

    if not os.path.exists(output_pdf) or os.path.getsize(output_pdf) == 0:
        raise RuntimeError('Ghostscript não gerou saída válida')


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
        current_app.logger.warning('[compress] qpdf flatten falhou: %s — copiando original', e)
        shutil.copyfile(src, dst)


def _qpdf_optimize_lossless(src: str, dst: str) -> None:
    qpdf = _get_qpdf_cmd()
    if not qpdf:
        shutil.copyfile(src, dst)
        return
    try:
        subprocess.run(
            [qpdf, '--silent', '--object-streams=generate',
             '--stream-data=compress', '--compress-streams=y', src, dst],
            check=True, capture_output=True, text=True, timeout=QPDF_TIMEOUT,
        )
    except Exception as e:
        current_app.logger.warning('[compress] qpdf lossless falhou: %s — copiando original', e)
        shutil.copyfile(src, dst)


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
      - resize_to_a4_suggested: se resize faz sentido para páginas muito grandes

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
            resize_suggested  = True
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
        p['resize_to_a4'] = resize_suggested

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
) -> None:
    upload_folder = os.path.dirname(output_path)

    # Frente 1 — piso mínimo de DPI
    # Grupos pequenos (especialmente páginas isoladas) são mais vulneráveis
    # a destruição visual por downsampling excessivo.
    effective_dpi = dpi
    if dpi < MIN_SAFE_DPI:
        current_app.logger.warning(
            '[compress-group] dpi=%d abaixo do piso seguro (%d) — elevando para %d. pages=%s',
            dpi, MIN_SAFE_DPI, MIN_SAFE_DPI, pages,
        )
        effective_dpi = MIN_SAFE_DPI

    params  = _build_gs_image_params(quality, effective_dpi)

    # Frente 3 — medir tamanho do grupo ANTES da compressão
    extracted_path = os.path.join(upload_folder, f'extracted_{uuid.uuid4().hex}.pdf')
    rotated_path   = os.path.join(upload_folder, f'rotated_{uuid.uuid4().hex}.pdf')
    _extract_pages(input_path, pages, extracted_path)
    _apply_rotations_pikepdf(extracted_path, pages, rotations, rotated_path)

    # size_in mede o grupo isolado (não o documento inteiro)
    size_in = os.path.getsize(rotated_path)

    current_app.logger.info(
        '[compress-group] pages=%s size_in=%.1f KB '
        'quality=%d dpi_req=%d dpi_eff=%d → jpeg_q=%d qfactor=%.4f '
        'color_res=%d gray_res=%d downsample=%s hsamples=%s vsamples=%s',
        pages, size_in / 1024,
        quality, dpi, effective_dpi,
        params['jpeg_q'], params['qfactor'],
        params['color_res'], params['gray_res'], params['downsample'],
        params['hsamples'], params['vsamples'],
    )

    try:
        _run_ghostscript(rotated_path, output_path, quality=quality, dpi=effective_dpi)
        size_out = os.path.getsize(output_path)
        reduction = (1 - size_out / size_in) * 100 if size_in else 0

        current_app.logger.info(
            '[compress-group] pages=%s size_before=%.1f KB size_after=%.1f KB reduction=%.1f%%',
            pages, size_in / 1024, size_out / 1024, reduction,
        )

        # Frente 2 — fallback de segurança por grupo
        # Casos que acionam fallback:
        #   a) GS inflou o arquivo (size_out >= size_in)
        #   b) Resultado suspeito: abaixo de MIN_GROUP_SIZE_KB absolutos
        #   c) Resultado suspeito: abaixo de MIN_GROUP_SIZE_RATIO do original
        fallback_reason = None
        if size_out >= size_in:
            fallback_reason = f'gs_larger (before={size_in/1024:.1f} KB after={size_out/1024:.1f} KB)'
        elif size_out < MIN_GROUP_SIZE_KB * 1024:
            fallback_reason = (
                f'suspiciously_small (after={size_out/1024:.1f} KB '
                f'< threshold={MIN_GROUP_SIZE_KB} KB)'
            )
        elif size_in > 0 and (size_out / size_in) < MIN_GROUP_SIZE_RATIO:
            fallback_reason = (
                f'ratio_too_low (after={size_out/1024:.1f} KB '
                f'ratio={size_out/size_in:.3f} < threshold={MIN_GROUP_SIZE_RATIO})'
            )

        if fallback_reason:
            current_app.logger.warning(
                '[compress-group] fallback=original pages=%s reason=%s — '
                'mantendo versão original do grupo',
                pages, fallback_reason,
            )
            shutil.copyfile(rotated_path, output_path)

    finally:
        for p in (extracted_path, rotated_path):
            try:
                os.remove(p)
            except OSError:
                pass


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
) -> str:
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
            sanitize_pdf(input_path, sanitized_path)
            cleanup.append(sanitized_path)
        except Exception as e:
            current_app.logger.warning('[compress] sanitize falhou: %s — usando original', e)
            shutil.copyfile(input_path, sanitized_path)
            cleanup.append(sanitized_path)
    else:
        shutil.copyfile(input_path, sanitized_path)
        cleanup.append(sanitized_path)

    # qpdf flatten
    flat_path = os.path.join(upload_folder, f'flat_{basename}.pdf')
    _qpdf_flatten(sanitized_path, flat_path)
    cleanup.append(flat_path)
    stage_source = flat_path

    # Rotações
    if rotations:
        rot_path = os.path.join(upload_folder, f'rot_{basename}.pdf')
        _apply_rotations_pikepdf(stage_source, None, rotations, rot_path)
        cleanup.append(rot_path)
        stage_source = rot_path

    # Lossless
    if internal_profile == 'lossless':
        out_path = os.path.join(upload_folder, f'comprimido_{basename}_{uuid.uuid4().hex}.pdf')
        _qpdf_optimize_lossless(stage_source, out_path)
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
        return out_path

    # Ghostscript
    prof    = PROFILES.get(internal_profile, PROFILES['equilibrio'])
    quality = prof['quality']
    dpi     = prof['dpi']
    out_gs  = os.path.join(upload_folder, f'comprimido_{basename}_{uuid.uuid4().hex}.pdf')

    try:
        _run_ghostscript(stage_source, out_gs, quality=quality, dpi=dpi)
        pages_after = _page_count(out_gs)
        size_after  = os.path.getsize(out_gs)
        reduction   = (1 - size_after / original_size) * 100 if original_size else 0

        current_app.logger.info(
            '[compress] gs done pages_before=%d pages_after=%d '
            'size_before=%.1f KB size_after=%.1f KB reduction=%.1f%%',
            original_pages, pages_after,
            original_size / 1024, size_after / 1024, reduction,
        )

        if size_after >= original_size:
            current_app.logger.info('[compress] fallback=gs_larger — entregando original')
            shutil.copyfile(stage_source, out_gs)

        return out_gs

    except Exception as exc:
        current_app.logger.error('[compress] GS falhou: %s', exc)
        shutil.copyfile(stage_source, out_gs)
        return out_gs

    finally:
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass