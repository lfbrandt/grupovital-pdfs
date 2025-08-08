# app/services/converter_service.py
import os
import tempfile
import subprocess
import shutil
from PIL import Image

IMG_EXTS  = {'jpg','jpeg','png','bmp','tif','tiff'}
DOC_EXTS  = {'doc','docx','odt','rtf','txt','html','ppt','pptx','odp'}

def _save_upload_to_tmp(upload_file, suffix):
    """Salva o arquivo de upload em um arquivo temporário fechado (Windows-safe) e retorna o caminho."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)  # fecha o handle imediatamente (Windows não gosta de arquivo aberto)
    upload_file.stream.seek(0)
    with open(path, 'wb') as out:
        shutil.copyfileobj(upload_file.stream, out)
    return path

def _tmp_pdf_path():
    fd, path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)
    return path

def _image_to_pdf(in_path, out_path):
    # Converte imagens (inclui multipágina para TIFF)
    img = Image.open(in_path)
    try:
        if img.format and img.format.upper() in ('TIFF', 'TIF'):
            # multipágina
            pages = []
            try:
                i = 0
                while True:
                    img.seek(i)
                    pages.append(img.convert('RGB'))
                    i += 1
            except EOFError:
                pass
            if not pages:
                raise ValueError("TIFF vazio")
            pages[0].save(out_path, save_all=True, append_images=pages[1:], format='PDF', resolution=300.0)
        else:
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(out_path, 'PDF', resolution=300.0)
    finally:
        img.close()

def _office_to_pdf(in_path, out_path):
    # Usa LibreOffice/soffice headless
    out_dir = os.path.dirname(out_path)
    cmd = [
        'soffice', '--headless', '--nologo', '--nofirststartwizard',
        '--convert-to', 'pdf', '--outdir', out_dir, in_path
    ]
    subprocess.run(cmd, check=True)
    produced = os.path.join(out_dir, os.path.splitext(os.path.basename(in_path))[0] + '.pdf')
    if not os.path.exists(produced):
        raise RuntimeError("Conversão não gerou PDF")
    # move/rename para o path final (p/ garantir nome e .cleanup)
    if os.path.exists(out_path):
        os.remove(out_path)
    shutil.move(produced, out_path)

def converter_doc_para_pdf(upload_file, modificacoes=None):
    """
    Converte imagens, docs e apresentações para PDF.
    Retorna caminho do PDF gerado (caller faz cleanup).
    """
    name = upload_file.filename or 'arquivo'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
    out_path = _tmp_pdf_path()

    try:
        if ext in IMG_EXTS:
            _image_to_pdf(in_path, out_path)
        elif ext in DOC_EXTS:
            _office_to_pdf(in_path, out_path)
        else:
            raise ValueError(f'Extensão não suportada para este conversor: {ext}')
        return out_path
    finally:
        # Só apaga o input depois que a conversão terminou e TODOS os handles foram fechados
        try:
            os.remove(in_path)
        except OSError:
            pass

def converter_planilha_para_pdf(upload_file, modificacoes=None):
    """
    Mantém sua implementação atual (XLS/XLSX/ODS/CSV) — apenas garanta a mesma
    estratégia de fechar/apagar: salvar em tmp fechado, converter, e só então os.remove.
    """
    # Exemplo: reutilizar o LibreOffice como acima.
    name = upload_file.filename or 'planilha'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
    out_path = _tmp_pdf_path()

    try:
        _office_to_pdf(in_path, out_path)
        return out_path
    finally:
        try:
            os.remove(in_path)
        except OSError:
            pass