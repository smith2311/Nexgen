"""Markdown -> PDF for docs/. See CICD_PIPELINE_GUIDE.md section 12 for usage."""
import sys
import markdown
from weasyprint import HTML

src, out = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()

html_body = markdown.markdown(
    text,
    extensions=["tables", "fenced_code", "toc", "attr_list", "sane_lists"],
    extension_configs={"toc": {"title": "Contents", "toc_depth": "2-3"}},
)

CSS = """
@page {
  size: A4; margin: 17mm 16mm 18mm 16mm;
  @bottom-center { content: counter(page) " / " counter(pages);
                   font-family: 'DejaVu Sans'; font-size: 7.5pt; color: #8a8a8a; }
  @top-right     { content: "NexGen CI/CD Pipeline Guide";
                   font-family: 'DejaVu Sans'; font-size: 7.5pt; color: #a0a0a0; }
}
body { font-family: 'DejaVu Sans', sans-serif; font-size: 9.3pt; line-height: 1.45;
       color: #1f2328; }
h1 { font-size: 19pt; color: #0f3d61; border-bottom: 2.5px solid #0f3d61;
     padding-bottom: 5px; margin: 0 0 14px; }
h2 { font-size: 13pt; color: #0f3d61; margin: 20px 0 7px;
     border-bottom: 1px solid #cfd8e0; padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 10.8pt; color: #14507f; margin: 14px 0 5px; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.2pt;
       background: #f2f4f7; padding: 1px 3.5px; border-radius: 2px; color: #9b2c2c; }
pre { background: #f7f8fa; border: 1px solid #dde3e9; border-left: 3px solid #0f3d61;
      padding: 8px 10px; font-size: 7.9pt; line-height: 1.35; overflow-wrap: break-word;
      white-space: pre-wrap; page-break-inside: avoid; margin: 8px 0; }
pre code { background: none; padding: 0; color: #1f2328; font-size: 7.9pt; }
table { border-collapse: collapse; width: 100%; margin: 9px 0; font-size: 8.4pt;
        page-break-inside: avoid; }
th { background: #0f3d61; color: #fff; text-align: left; padding: 5px 7px;
     font-weight: bold; }
td { border: 1px solid #dde3e9; padding: 4.5px 7px; vertical-align: top; }
tr:nth-child(even) td { background: #f7f9fb; }
strong { color: #0b2f4d; }
ul, ol { margin: 6px 0 6px 0; padding-left: 19px; }
li { margin: 2.5px 0; }
hr { border: none; border-top: 1px solid #dde3e9; margin: 15px 0; }
a { color: #14507f; text-decoration: none; }
.toc { background: #f7f9fb; border: 1px solid #dde3e9; padding: 8px 12px;
       page-break-inside: avoid; font-size: 8.6pt; }
.toc > ul { margin: 3px 0; }
.toc ul ul { padding-left: 15px; }
blockquote { border-left: 3px solid #c9a227; background: #fdfaf0; margin: 8px 0;
             padding: 6px 10px; }
"""

doc = "<html><head><meta charset='utf-8'><style>%s</style></head><body>%s</body></html>" % (CSS, html_body)
HTML(string=doc, base_url=".").write_pdf(out)
print("wrote", out)
