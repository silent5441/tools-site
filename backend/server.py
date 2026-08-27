import os
import io
import tempfile
import base64
import subprocess
import pikepdf
from pypdf import PdfReader, PdfWriter
from PIL import Image
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/merge-pdf', methods=['POST'])
def merge_pdf():
    try:
        files = request.files.getlist('files')
        if not files or len(files) < 2:
            return jsonify({'error': 'Upload at least 2 PDF files'}), 400
        merger = PdfWriter()
        for f in files:
            reader = PdfReader(f.stream)
            for page in reader.pages:
                merger.add_page(page)
        output = io.BytesIO()
        merger.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='merged.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/split-pdf', methods=['POST'])
def split_pdf():
    try:
        f = request.files.get('file')
        pages_str = request.form.get('pages', '')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        reader = PdfReader(f.stream)
        total = len(reader.pages)
        pages = parse_pages(pages_str, total)
        writer = PdfWriter()
        for p in pages:
            if 1 <= p <= total:
                writer.add_page(reader.pages[p - 1])
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='split.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/compress-pdf', methods=['POST'])
def compress_pdf():
    try:
        f = request.files.get('file')
        quality = request.form.get('quality', 'medium')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400

        scale_map = {'low': 1.0, 'medium': 1.5, 'high': 2.0}
        jpeg_map = {'low': 0.7, 'medium': 0.5, 'high': 0.3}
        scale = scale_map.get(quality, 1.5)
        jpeg_q = jpeg_map.get(quality, 0.5)

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        img_dir = tempfile.mkdtemp()
        subprocess.run([
            'pdftoppm', '-jpeg', '-r', str(int(72 * scale)), tmp_path,
            os.path.join(img_dir, 'page')
        ], check=True, capture_output=True)

        img_files = sorted([os.path.join(img_dir, x) for x in os.listdir(img_dir) if x.endswith('.jpg')])

        images = []
        for img_path in img_files:
            img = Image.open(img_path)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=int(jpeg_q * 100), optimize=True)
            buf.seek(0)
            images.append((buf, img.size))

        if not images:
            return jsonify({'error': 'Could not render pages'}), 500

        reader = PdfReader(tmp_path)
        first_img, (w_px, h_px) = images[0]
        pdf_img = Image.open(images[0][0])
        page_w = float(reader.pages[0].mediabox.width)
        page_h = float(reader.pages[0].mediabox.height)

        writer = PdfWriter()
        for i, (img_buf, (wpx, hpx)) in enumerate(images):
            pdf_buf = io.BytesIO()
            img = Image.open(img_buf)
            img.save(pdf_buf, format='PDF')
            pdf_buf.seek(0)
            page_reader = PdfReader(pdf_buf)
            writer.add_page(page_reader.pages[0])

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)

        orig_size = os.path.getsize(tmp_path)
        comp_size = len(output.getvalue())

        os.unlink(tmp_path)
        for p in img_files:
            os.unlink(p)
        os.rmdir(img_dir)

        return send_file(
            io.BytesIO(output.getvalue()),
            mimetype='application/pdf',
            as_attachment=True,
            download_name='compressed.pdf',
            headers={
                'X-Original-Size': str(orig_size),
                'X-Compressed-Size': str(comp_size)
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rotate-pdf', methods=['POST'])
def rotate_pdf():
    try:
        f = request.files.get('file')
        angle = int(request.form.get('angle', '90'))
        pages_str = request.form.get('pages', 'all')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        reader = PdfReader(f.stream)
        total = len(reader.pages)
        if pages_str == 'all':
            page_nums = list(range(1, total + 1))
        else:
            page_nums = parse_pages(pages_str, total)
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if (i + 1) in page_nums:
                page.rotate(angle)
            writer.add_page(page)
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='rotated.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete-pages', methods=['POST'])
def delete_pages():
    try:
        f = request.files.get('file')
        pages_str = request.form.get('pages', '')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        reader = PdfReader(f.stream)
        total = len(reader.pages)
        delete_pages = parse_pages(pages_str, total)
        keep = [i for i in range(total) if (i + 1) not in delete_pages]
        if not keep:
            return jsonify({'error': 'Cannot delete all pages'}), 400
        writer = PdfWriter()
        for i in keep:
            writer.add_page(reader.pages[i])
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='modified.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pdf-to-text', methods=['POST'])
def pdf_to_text():
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        reader = PdfReader(f.stream)
        text = ''
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + '\n\n'
        return jsonify({'text': text, 'pages': len(reader.pages)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pdf-to-image', methods=['POST'])
def pdf_to_image():
    try:
        f = request.files.get('file')
        fmt = request.form.get('format', 'jpg')
        scale = int(request.form.get('scale', '2'))
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        dpi = 72 * scale
        out_prefix = os.path.join(tempfile.mkdtemp(), 'page')
        subprocess.run(['pdftoppm', '-' + fmt, '-r', str(dpi), tmp_path, out_prefix], check=True, capture_output=True)
        ext = '.jpg' if fmt == 'jpg' else '.png'
        img_files = sorted([os.path.join(out_prefix.rsplit('/', 1)[0], x) for x in os.listdir(os.path.dirname(out_prefix)) if x.endswith(ext)])
        if len(img_files) == 1:
            return send_file(img_files[0], mimetype='image/' + fmt, as_attachment=True, download_name='page1.' + fmt)
        import zipfile
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, img_path in enumerate(img_files):
                zf.write(img_path, f'page_{idx+1}.{fmt}')
        zip_buf.seek(0)
        os.unlink(tmp_path)
        for p in img_files:
            os.unlink(p)
        return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name='images.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/image-to-pdf', methods=['POST'])
def image_to_pdf():
    try:
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No images uploaded'}), 400
        images = []
        for f in files:
            img = Image.open(f.stream)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            images.append(img)
        if not images:
            return jsonify({'error': 'No valid images'}), 400
        output = io.BytesIO()
        images[0].save(output, format='PDF', save_all=True, append_images=images[1:] if len(images) > 1 else [])
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='images.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/watermark-pdf', methods=['POST'])
def watermark_pdf():
    try:
        f = request.files.get('file')
        text = request.form.get('text', 'WATERMARK')
        opacity = float(request.form.get('opacity', '0.3'))
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400

        reader = PdfReader(f.stream)
        writer = PdfWriter()

        for page in reader.pages:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)

            watermark_buf = io.BytesIO()
            img = Image.new('RGBA', (int(w), int(h)), (255, 255, 255, 0))
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            except:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            alpha = int(opacity * 255)
            draw.text(((w - tw) / 2, (h - th) / 2), text, fill=(128, 128, 128, alpha), font=font)
            img_rgb = img.convert('RGB')
            img_rgb.save(watermark_buf, format='PDF')
            watermark_buf.seek(0)
            wm_reader = PdfReader(watermark_buf)
            page.merge_page(wm_reader.pages[0])
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='watermarked.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/protect-pdf', methods=['POST'])
def protect_pdf():
    try:
        f = request.files.get('file')
        user_pwd = request.form.get('user_password', '')
        owner_pwd = request.form.get('owner_password', '')
        allow_print = request.form.get('allow_print', 'false') == 'true'
        allow_copy = request.form.get('allow_copy', 'false') == 'true'
        allow_edit = request.form.get('allow_edit', 'false') == 'true'

        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        if not user_pwd or len(user_pwd) < 4:
            return jsonify({'error': 'Password must be at least 4 characters'}), 400

        pdf = pikepdf.open(f.stream)

        perm = pikepdf.Permissions(
            modify_all=allow_edit,
            copy=allow_copy,
            print=allow_print
        )

        output = io.BytesIO()
        pdf.save(output, encryption=pikepdf.Encryption(
            owner=owner_pwd or user_pwd + '-owner',
            user=user_pwd,
            R=6,
            permissions=perm
        ))
        output.seek(0)

        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='protected.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/image-to-text', methods=['POST'])
def image_to_text():
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        try:
            import pytesseract
            img = Image.open(f.stream)
            text = pytesseract.image_to_string(img)
            return jsonify({'text': text})
        except ImportError:
            return jsonify({'error': 'OCR not available on server'}), 501
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def parse_pages(pages_str, total):
    pages = set()
    if not pages_str:
        return list(range(1, total + 1))
    for part in pages_str.split(','):
        part = part.strip()
        if '-' in part:
            try:
                a, b = part.split('-', 1)
                a, b = int(a.strip()), int(b.strip())
                for i in range(a, min(b, total) + 1):
                    if i >= 1:
                        pages.add(i)
            except:
                pass
        else:
            try:
                n = int(part)
                if 1 <= n <= total:
                    pages.add(n)
            except:
                pass
    return sorted(pages)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
