"""构造一份"图片型 PDF"当测试样本。

408 真题是扫描件：PDF 里没有文本层，只有整页图片。
这里用 PIL 把题目画成图片，再整页塞进 PDF，效果等价（有题号、有页脚、有跨页题、有小问）。

用法：
    .venv\\Scripts\\python tests\\make_sample_pdf.py [输出路径]
默认输出 data/uploads/sample_2009.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

W, H = 1240, 1754  # A4 @150dpi
LEFT = 90
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到中文字体，请修改 FONT_CANDIDATES")


def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("L", (W, H), 255)
    return img, ImageDraw.Draw(img)


def page1(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> None:
    y = 110
    lines = [
        "1. 下列关于数据结构的说法中，正确的是（  ）",
        "A. 数据的逻辑结构与存储结构一一对应",
        "B. 顺序存储的线性表一定可以随机访问",
        "C. 单链表的插入操作时间复杂度为 O(1)",
        "D. 栈和队列都是非线性结构",
    ]
    for text in lines:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46

    y += 20
    lines = [
        "2. 在长度为 n 的顺序表中删除第 i 个元素，需要移动的元素个数为（  ）",
        "A. n-i        B. n-i+1        C. i        D. i-1",
    ]
    for text in lines:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46

    # 第 3 题故意写很长，越过页底，下一题在第 2 页，用来测跨页合并
    y += 30
    long_q3 = [
        "3. 已知一棵二叉树的先序遍历序列为 ABDECF，中序遍历序列为 DBEAFC。",
        "（续）请完成下列各小题：",
        "(1) 画出该二叉树的结构，并写出各结点的层次编号。",
        "(2) 求该二叉树的后序遍历序列，并说明理由。",
        "(3) 该二叉树是否为完全二叉树？请给出判断过程。",
        "(4) 若将该二叉树用二叉链表存储，请写出求树高的非递归算法思路。",
        "(5) 分析你所写算法的时间复杂度与空间复杂度。",
        "(6) 若把该树改造为二叉排序树，请给出插入序列的一种可行方案。",
        "(7) 讨论二叉排序树在最好与最坏情况下的查找长度。",
        "(8) 请说明平衡二叉树的旋转操作如何改善查找性能。",
        "(9) 结合上问，总结树形结构在检索中的应用。",
        "(10) 请写出本题的完整解答过程。",
    ]
    for text in long_q3:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46


def page2(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> None:
    y = 110
    for text in ["3. （接上页）(11) 请总结本题涉及的遍历方法。", "(12) 给出结论。"]:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46

    y += 40
    lines = [
        "4. (8分) 设某计算机主存地址空间为 4MB，按字节编址，Cache 采用直接映射方式，",
        "数据区容量为 16KB，每个主存块大小为 32B。请回答下列问题：",
    ]
    for text in lines:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46
    for text in [
        "(1) 请计算 Cache 的行数。",
        "(2) 请计算主存地址的字段划分。",
        "(3) 说明该映射方式的优缺点。",
    ]:
        draw.text((LEFT + 30, y), text, font=font, fill=0)
        y += 46

    y += 40
    for text in [
        "5. 下列关于进程调度的说法中，错误的是（  ）",
        "A. 时间片轮转调度适用于分时系统",
        "B. 短作业优先调度可能导致饥饿",
        "C. 高响应比优先调度是非抢占式的",
        "D. 多级反馈队列调度一定能保证公平",
    ]:
        draw.text((LEFT, y), text, font=font, fill=0)
        y += 46

    draw.text((LEFT, 1700), "第 2 页 共 2 页", font=font, fill=0)  # 页脚，测试裁剪


def build(out_path: Path) -> Path:
    font = load_font(30)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()
    tmp = out_path.parent / "_sample_page.png"
    for index, painter in enumerate((page1, page2), start=1):
        img, draw = new_page()
        painter(draw, font)
        if index == 1:
            draw.text((LEFT, 1700), "第 1 页 共 2 页", font=font, fill=0)
        img.save(tmp)
        page = doc.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(0, 0, 595, 842), filename=str(tmp))
    doc.save(out_path)
    doc.close()
    tmp.unlink(missing_ok=True)
    return out_path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/uploads/sample_2009.pdf")
    print("已生成:", build(target))
