# 开题报告相关产物

| 文件 | 用途 |
|---|---|
| `report-body.html` | 开题报告正文源文件（结构化 HTML，写入 docx 的原始内容） |
| `module-figure.png` | 图1 系统功能模块图（800×914，横向树） |
| `make_module_figure.py` | 重新生成上图。`python make_module_figure.py` |

成品文档在 `C:/Users/lin/Desktop/林旭焕-开题报告.docx`，不在仓库内。

改动正文时：先改 `report-body.html`，再按段落块重新写入 docx；图要改则改 `make_module_figure.py` 后重跑，再替换文档中的图片。
