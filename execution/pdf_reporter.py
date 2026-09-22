# execution/pdf_reporter.py
import os
import time
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader

from logging_config import get_logger, resource_path
logger = get_logger(__name__)

class PDFReporter:
    def __init__(self, logo_filename="InsituMicronlogo.jpg"):
        self.styles = getSampleStyleSheet()
        self.logo_path = resource_path(logo_filename)
        
        # Create custom styles
        self.styles.add(ParagraphStyle(name='SectionTitle', 
                                       parent=self.styles['Heading2'],
                                       textColor=colors.HexColor('#2c3e50'),
                                       spaceAfter=10))
        
        self.styles.add(ParagraphStyle(name='TableCaption', 
                                       parent=self.styles['Italic'],
                                       alignment=1,
                                       spaceBefore=10,
                                       spaceAfter=10))

    def generate_report(self, save_path: str, metadata: dict, results: dict, comments: str, graph_path: str, report_type: str = "creep"):
        """
        Generate a professional PDF report for Creep or Tensile test results.
        
        save_path   : destination file path (.pdf)
        metadata    : dict containing sample info ('filename', 'area', 'gauge_length', 'date', etc.)
        results     : dict containing property values (e.g. creep rates or tensile moduli)
        comments    : user comments string
        graph_path  : path to the saved pyqtgraph image
        report_type : 'creep' or 'tensile'
        """
        try:
            doc = SimpleDocTemplate(save_path, pagesize=letter,
                                    rightMargin=0.75*inch, leftMargin=0.75*inch,
                                    topMargin=1.1*inch, bottomMargin=0.75*inch)
            elements = []

            # 1. Header is handled by canvas callbacks, so we just add the title
            title_style = ParagraphStyle(name='CenteredTitle', parent=self.styles['Heading1'], alignment=1)
            elements.append(Spacer(1, 0.2*inch))  # Space for the logo in the header
            doc_title = "<b>Creep & Strain Rate Test Report</b>" if report_type == "creep" else "<b>Material Tensile Test Report</b>"
            elements.append(Paragraph(doc_title, title_style))
            elements.append(Spacer(1, 0.2*inch))
            
            # Line separator
            elements.append(Table([['']], colWidths=[7*inch], style=[
                ('LINEABOVE', (0, 0), (-1, -1), 1, colors.HexColor('#2c3e50'))
            ]))
            elements.append(Spacer(1, 0.2*inch))

            # 2. Test Metadata
            elements.append(Paragraph("Test Details", self.styles['SectionTitle']))
            meta_data = [
                ["Source File:", metadata.get('filename', 'Unknown'), "Test Date:", metadata.get('date', time.strftime("%Y-%m-%d %H:%M:%S"))],
                ["Sample ID:", metadata.get('sample_id', '--'), "Sample Type:", metadata.get('sample_type', '--')],
                ["Cross-Section Area:", f"{metadata.get('area', '--')} mm²", "Gauge Length:", f"{metadata.get('gauge_length', '--')} mm"]
            ]
            
            s_type = metadata.get('sample_type', '')
            if s_type == 'Rectangular':
                meta_data.insert(2, ["Width:", f"{metadata.get('width', '--')} mm", "Thickness:", f"{metadata.get('thickness', '--')} mm"])
            elif s_type == 'Circular':
                diam = metadata.get('diameter')
                if diam is None and 'radius' in metadata:
                    try:
                        diam = f"{float(metadata['radius']) * 2.0:.3f}"
                    except (ValueError, TypeError):
                        diam = str(metadata['radius'])
                meta_data.insert(2, ["Diameter:", f"{diam} mm" if diam is not None else "--", "", ""])
            elif s_type == 'Custom / Direct Area':
                meta_data.insert(2, ["Direct Area Spec:", f"{metadata.get('custom_area', metadata.get('area', '--'))} mm²", "", ""])

            temp = metadata.get('temperature')
            stress = metadata.get('target_stress')
            weight = metadata.get('dead_weight')
            if temp or stress:
                meta_data.append([
                    "Temperature:", f"{temp} °C" if temp else "--",
                    "Target Stress:", f"{stress} MPa" if stress else "--"
                ])
            if weight:
                meta_data.append(["Dead Weight:", f"{weight} kg", "", ""])
                
            meta_table = Table(meta_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
            meta_table.setStyle(TableStyle([
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
                ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor('#34495e')),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6)
            ]))
            elements.append(meta_table)
            elements.append(Spacer(1, 0.3*inch))

            # 3. Mechanical Properties Results
            if report_type == "creep":
                elements.append(Paragraph("Creep & Strain Rate Properties", self.styles['SectionTitle']))
                res_data = [
                    ["Creep Property", "Value", "Unit"],
                    ["Steady-State Creep Rate (dε/dt)", f"{results.get('steady_state_rate_hr', '--')}", "%/hr"],
                    ["Steady-State Creep Rate (1/s)", f"{results.get('steady_state_rate_sec', '--')}", "1/s"],
                    ["Steady-State Linear Fit (R²)", f"{results.get('steady_state_r2', '--')}", "--"],
                    ["Mean Hold Stress", f"{results.get('mean_stress', '--')}", "MPa"],
                    ["Total Creep Strain", f"{results.get('total_strain', '--')}", "%"],
                    ["Test Duration / Rupture Time", f"{results.get('test_duration', '--')}", "hr"],
                    ["Rupture Status", f"{results.get('rupture_status', '--')}", "--"]
                ]
            else:
                elements.append(Paragraph("Mechanical Properties", self.styles['SectionTitle']))
                res_data = [
                    ["Property", "Value", "Unit"],
                    ["Young's Modulus (E)", f"{results.get('e_modulus', '--')}", "MPa"],
                    ["Yield Strength (0.2% Offset)", f"{results.get('yield_strength', '--')}", "MPa"],
                    ["Ultimate Tensile Strength (UTS)", f"{results.get('uts', '--')}", "MPa"],
                    ["Break Stress", f"{results.get('break_stress', '--')}", "MPa"]
                ]
            
            res_table = Table(res_data, colWidths=[3.5*inch, 1.5*inch, 1.5*inch])
            res_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
                ('GRID', (0, 0), (-1, -1), 1, colors.white)
            ]))
            elements.append(res_table)
            elements.append(Spacer(1, 0.3*inch))

            # 4. Graph
            graph_elements = []
            graph_title = "Creep Curve — Strain vs. Time" if report_type == "creep" else "Stress-Strain Curve"
            graph_elements.append(Paragraph(graph_title, self.styles['SectionTitle']))
            if os.path.exists(graph_path):
                # Scale image to fit within page width (7 inches max)
                graph_img = Image(graph_path)
                
                # Keep aspect ratio
                aspect = graph_img.drawHeight / float(graph_img.drawWidth)
                graph_img.drawWidth = 6.5 * inch
                graph_img.drawHeight = 6.5 * inch * aspect
                
                graph_elements.append(graph_img)
            else:
                graph_elements.append(Paragraph("<i>Graph image not available.</i>", self.styles['Normal']))
            
            elements.append(KeepTogether(graph_elements))
            elements.append(Spacer(1, 0.3*inch))

            # 5. User Comments
            if comments and comments.strip():
                elements.append(Paragraph("Operator Notes", self.styles['SectionTitle']))
                
                # Wrap comments in a styled table to look like a comment box
                comment_table = Table([[Paragraph(comments, self.styles['Normal'])]], colWidths=[7*inch])
                comment_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9f9f9')),
                    ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
                    ('PADDING', (0, 0), (-1, -1), 10)
                ]))
                elements.append(comment_table)

            # Build PDF with page templates for header
            def add_header(canvas, doc):
                canvas.saveState()
                if os.path.exists(self.logo_path):
                    logo = ImageReader(self.logo_path)
                    # Position logo at top left (x=0.5 inch, y=10.3 inch)
                    canvas.drawImage(logo, 0.5*inch, 10.3*inch, width=1.75*inch, height=0.5*inch, preserveAspectRatio=True, mask='auto')
                canvas.restoreState()

            doc.build(elements, onFirstPage=add_header, onLaterPages=add_header)
            logger.info("Successfully generated PDF report at %s", save_path)
            return True
            
        except Exception as e:
            logger.error("Error generating PDF: %s", str(e))
            raise
