import os
import sys

# Ensure execution path is valid for importing
sys.path.insert(0, os.path.dirname(__file__))

from execution.pdf_reporter import PDFReporter

def test_pdf():
    reporter = PDFReporter()
    metadata = {'filename': 'test.csv', 'area': '10', 'gauge_length': '50', 'date': '2026-04-30 12:00:00'}
    results = {'e_modulus': '100', 'yield_strength': '50', 'uts': '60', 'break_stress': '40'}
    
    # create a dummy image
    from PIL import Image
    img = Image.new('RGB', (100, 100), color = 'red')
    img.save('dummy.png')
    
    reporter.generate_report('test_report.pdf', metadata, results, 'Test comments', 'dummy.png')
    print("Report generated. Exists:", os.path.exists('test_report.pdf'))

if __name__ == '__main__':
    test_pdf()
