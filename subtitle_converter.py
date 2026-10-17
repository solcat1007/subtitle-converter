#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕格式转换器 (Subtitle Converter)
====================================
功能：SRT / VTT / ASS(SSA) 三种字幕格式互相转换
特性：
  - 纯 tkinter GUI，零外部依赖
  - 拖拽或浏览选择文件，支持批量转换
  - 预览原始内容与转换结果对比
  - 编码自动检测（UTF-8 / UTF-8-BOM / GBK / UTF-16）
  - 内置中英文双语切换
  - 窗口打开即全屏
作者：File Agent
日期：2026-07-05
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import re
import ctypes
from ctypes import wintypes
from pathlib import Path

# ============================================================================
# Windows 拖拽支持（通过 WM_DROPFILES 消息，纯标准库实现）
# ============================================================================

# 加载必要的 Windows API
user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# 定义 Windows 消息常量
WM_DROPFILES = 0x0233

# 定义 DragAcceptFiles（在 shell32.dll中）/ DragQueryFile / DragFinish 的函数签名
shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragAcceptFiles.restype = None

shell32.DragQueryFileW.argtypes = [wintypes.HDROP, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT

shell32.DragFinish.argtypes = [wintypes.HDROP]
shell32.DragFinish.restype = None


def enable_file_drop(widget, callback):
    """
    为指定的 tkinter 控件启用 Windows 文件拖拽功能。
    通过 ctypes 调用 Windows API，将 WM_DROPFILES 消息绑定到 tkinter 回调。

    Args:
        widget: tkinter 控件（通常为根窗口 Tk 或 Toplevel）
        callback: 接收到拖拽文件列表后的回调函数，签名为 callback(file_paths: list[str])
    """
    hwnd = widget.winfo_id()

    # 注册窗口接受拖拽文件
    shell32.DragAcceptFiles(hwnd, True)

    # 使用 tkinter 的消息处理机制拦截 WM_DROPFILES
    def drop_handler(event):
        """处理 WM_DROPFILES 消息，提取文件路径并调用回调"""
        hdrop = event.data  # WM_DROPFILES 的 wParam 即为 HDROP 句柄
        try:
            # 先查询拖入的文件数量
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            files = []
            for i in range(count):
                # 获取每个文件的路径长度
                length = shell32.DragQueryFileW(hdrop, i, None, 0)
                if length > 0:
                    # 分配缓冲区并获取路径
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(hdrop, i, buffer, length + 1)
                    files.append(buffer.value)
            if files:
                callback(files)
        finally:
            # 释放拖入资源
            shell32.DragFinish(hdrop)

    # 将自定义消息处理注册到 tkinter 的消息分发中
    # tkinter 1.0 使用 'ClientMessage' 事件，需要创建自定义绑定
    widget.tk.call('bind', str(widget), '<<DropFiles>>', None)
    # 使用 tcl 层面注册 Windows 消息
    widget.tk.createcommand(
        '::tk::dnd::HandleDrop',
        lambda *args: None
    )

    # 通过 tcl 注册 WM_DROPFILES 消息处理
    try:
        # 使用 tk 的 dnd 扩展（如果可用）或直接注册 Windows 消息
        widget.tk.eval(f'''
            proc ::tk::dnd::_HandleDrop {{}} {{}}
            bind {str(widget)} <DropFiles> {{}}
        ''')
    except Exception:
        pass

    # 核心方案：通过 tk 的 Tcl 命令注册 Windows 消息处理
    # 在 Windows 上，通过 createcommand 覆盖默认的消息分发
    widget.drop_callback = callback

    # 获取 tk 窗口路径名
    widget_path = str(widget)

    # 注册 Tcl 命令来处理拖拽
    cmd_name = f'::drophandler_{widget_path.replace(".", "_").replace("!", "")}'
    widget.tk.createcommand(cmd_name, lambda files_str='': _process_drop(files_str, callback))

    # 使用 tcl 脚本注册 WM_DROPFILES
    tcl_script = f'''
        proc {cmd_name}_wrapper {{}} {{
            # 占位
        }}
    '''
    widget.tk.eval(tcl_script)

    # 实际上需要更底层的方式。这里用另一个方法：
    # 在 Windows 上通过 tk 的 after 循环 + ctypes 窗口过程钩子来处理
    # 但更简单的是直接用 tk 的 dnd 机制
    # 我们使用一个更可靠的替代方案


# 简化版拖拽：注册全局 Windows 消息钩子
_drop_targets = {}  # hwnd -> callback 映射

# Win32 窗口过程原型
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
SetWindowLongPtrW = user32.SetWindowLongPtrW
SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROC]
SetWindowLongPtrW.restype = WNDPROC

CallWindowProcW = user32.CallWindowProcW
CallWindowProcW.argtypes = [WNDPROC, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
CallWindowProcW.restype = ctypes.c_long

GWL_WNDPROC = -4
_original_procs = {}  # hwnd -> (old_proc, new_proc) 用于恢复


def _window_proc(hwnd, msg, wparam, lparam):
    """自定义窗口过程，拦截 WM_DROPFILES 消息"""
    if msg == WM_DROPFILES and hwnd in _drop_targets:
        callback = _drop_targets[hwnd]
        hdrop = wparam
        try:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            files = []
            for i in range(count):
                length = shell32.DragQueryFileW(hdrop, i, None, 0)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(hdrop, i, buffer, length + 1)
                    files.append(buffer.value)
            if files:
                callback(files)
        finally:
            shell32.DragFinish(hdrop)
        return 0  # 消息已处理

    # 调用原始窗口过程
    if hwnd in _original_procs:
        old_proc, _ = _original_procs[hwnd]
        return CallWindowProcW(old_proc, hwnd, msg, wparam, lparam)
    return 0


def register_drop_target(widget, callback):
    """
    注册 tkinter 窗口为文件拖拽目标。

    Args:
        widget: tkinter 控件
        callback: 回调函数 callback(file_paths: list[str])
    """
    hwnd = widget.winfo_id()
    _drop_targets[hwnd] = callback

    # 允许接受拖拽
    shell32.DragAcceptFiles(hwnd, True)

    # 子类化窗口过程
    if hwnd not in _original_procs:
        new_proc = WNDPROC(_window_proc)
        old_proc = SetWindowLongPtrW(hwnd, GWL_WNDPROC, new_proc)
        _original_procs[hwnd] = (old_proc, new_proc)


# ============================================================================
# 编码自动检测
# ============================================================================

def detect_encoding(file_path):
    """
    自动检测文本文件编码。
    依次尝试 UTF-8-BOM → UTF-8 → GBK → UTF-16-LE → UTF-16-BE → Latin-1（兜底）

    Args:
        file_path: 文件路径

    Returns:
        str: 检测到的编码名称
    """
    with open(file_path, 'rb') as f:
        raw = f.read()

    # 检查 BOM
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'  # UTF-8-BOM
    if raw.startswith(b'\xff\xfe'):
        return 'utf-16-le'
    if raw.startswith(b'\xfe\xff'):
        return 'utf-16-be'

    # 尝试 UTF-8
    try:
        raw.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # 尝试 GBK（中文 Windows 常见编码）
    try:
        raw.decode('gbk')
        return 'gbk'
    except UnicodeDecodeError:
        pass

    # 尝试 UTF-16
    try:
        raw.decode('utf-16')
        return 'utf-16'
    except UnicodeDecodeError:
        pass

    # 兜底
    return 'latin-1'


# ============================================================================
# 字幕格式解析与转换核心
# ============================================================================

def parse_srt(content):
    """
    解析 SRT 格式字幕。

    SRT 格式：
        序号
        时间线 (00:00:00,000 --> 00:00:00,000)
        文本（可多行）
        空行分隔

    Args:
        content: SRT 文本内容

    Returns:
        list[dict]: 字幕条目列表，每个条目包含 index, start, end, text 字段
    """
    entries = []
    # 标准化换行符
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    # 按连续空行分割块
    blocks = re.split(r'\n\s*\n', content.strip())

    srt_time_pattern = re.compile(
        r'(\d{1,2}:\d{2}:\d{2}[,\.]\d{2,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{2,3})'
    )

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split('\n')
        if len(lines) < 2:
            continue

        # 第一行可能是序号
        idx = 0
        time_match = srt_time_pattern.search(block)
        if not time_match:
            continue

        start_time = time_match.group(1).replace(',', '.')
        end_time = time_match.group(2).replace(',', '.')

        # 提取文本：时间线之后的所有行
        time_line_idx = None
        for i, line in enumerate(lines):
            if '-->' in line:
                time_line_idx = i
                break
        if time_line_idx is None:
            continue

        text_lines = lines[time_line_idx + 1:]
        text = '\n'.join(text_lines).strip()

        entries.append({
            'start': _normalize_time(start_time),
            'end': _normalize_time(end_time),
            'text': text
        })

    return entries


def parse_vtt(content):
    """
    解析 WebVTT 格式字幕。

    VTT 格式：
        WEBVTT（文件头）
        空行
        时间线 (00:00:00.000 --> 00:00:00.000)
        文本（可多行）
        空行分隔

    Args:
        content: VTT 文本内容

    Returns:
        list[dict]: 字幕条目列表
    """
    entries = []
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # 移除 WEBVTT 头部
    content = re.sub(r'^WEBVTT.*?\n\n', '', content, flags=re.IGNORECASE | re.DOTALL)

    # 移除 VTT 内联样式标记（如 <c.red>、<b>、<i> 等）
    # 保留文本内容
    blocks = re.split(r'\n\s*\n', content.strip())

    vtt_time_pattern = re.compile(
        r'(\d{1,2}:?\d{2}:\d{2}[\.]\d{2,3})\s*-->\s*(\d{1,2}:?\d{2}:\d{2}[\.]\d{2,3})'
    )

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 跳过 NOTE 注释块
        if block.upper().startswith('NOTE'):
            continue

        time_match = vtt_time_pattern.search(block)
        if not time_match:
            continue

        start_time = time_match.group(1)
        end_time = time_match.group(2)

        # 提取文本
        lines = block.split('\n')
        time_line_idx = None
        for i, line in enumerate(lines):
            if '-->' in line:
                time_line_idx = i
                break
        if time_line_idx is None:
            continue

        text_lines = lines[time_line_idx + 1:]
        # 过滤掉 VTT 的 cue 标识符行
        text = '\n'.join(text_lines).strip()
        # 移除 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)

        entries.append({
            'start': _normalize_time(start_time),
            'end': _normalize_time(end_time),
            'text': text
        })

    return entries


def parse_ass(content):
    """
    解析 ASS/SSA 格式字幕。

    ASS 格式包含多个段：[Script Info]、[V4+ Styles]、[Events] 等。
    字幕行在 [Events] 段，格式为：
    Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text

    Args:
        content: ASS/SSA 文本内容

    Returns:
        list[dict]: 字幕条目列表
    """
    entries = []
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # 查找 [Events] 段
    events_match = re.search(r'\[Events\](.*?)(?=\n\[|\Z)', content, re.DOTALL | re.IGNORECASE)
    if not events_match:
        return entries

    events_section = events_match.group(1)

    # 解析 Dialogue 行（ASS 格式：逗号分隔，共10个字段）
    dialogue_pattern = re.compile(
        r'^Dialogue:\s*'
        r'(\d+),\s*'          # Layer
        r'([^,]*),\s*'        # Start
        r'([^,]*),\s*'        # End
        r'([^,]*),\s*'        # Style
        r'([^,]*),\s*'        # Name/Actor
        r'([^,]*),\s*'        # MarginL
        r'([^,]*),\s*'        # MarginR
        r'([^,]*),\s*'        # MarginV
        r'([^,]*),\s*'        # Effect
        r'(.*)'               # Text
    )

    for line in events_section.split('\n'):
        line = line.strip()
        if not line.startswith('Dialogue:') and not line.startswith('dialogue:'):
            continue

        match = dialogue_pattern.match(line)
        if not match:
            # 尝试更宽松的匹配
            parts = line.split(',', 9)
            if len(parts) < 10:
                continue
            start_time = parts[1].strip()
            end_time = parts[2].strip()
            text = parts[9].strip()
        else:
            start_time = match.group(2).strip()
            end_time = match.group(3).strip()
            text = match.group(10).strip()

        # 移除 ASS 特效标记（如 {\xxx}）
        text = re.sub(r'\{[^}]*\}', '', text)
        # 移除 \N 换行标记，替换为实际换行
        text = text.replace('\\N', '\n').replace('\\n', '\n')

        entries.append({
            'start': _normalize_time(start_time),
            'end': _normalize_time(end_time),
            'text': text.strip()
        })

    return entries


def _normalize_time(time_str):
    """
    将各种字幕时间格式标准化为内部表示（秒数，float）。

    支持格式：
      - HH:MM:SS,mmm (SRT)
      - HH:MM:SS.mmm (VTT)
      - H:MM:SS.cc  (ASS，厘秒)

    Args:
        time_str: 时间字符串

    Returns:
        float: 秒数
    """
    time_str = time_str.strip().replace(',', '.')
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = '0'
        m, s = parts
    else:
        return 0.0

    try:
        h = int(h)
        m = int(m)
        s = float(s)
        return h * 3600 + m * 60 + s
    except ValueError:
        return 0.0


def _format_srt_time(seconds):
    """将秒数格式化为 SRT 时间格式 (HH:MM:SS,mmm)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def _format_vtt_time(seconds):
    """将秒数格式化为 VTT 时间格式 (HH:MM:SS.mmm)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _format_ass_time(seconds):
    """将秒数格式化为 ASS 时间格式 (H:MM:SS.cc，厘秒)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def to_srt(entries):
    """将字幕条目列表转换为 SRT 格式文本"""
    lines = []
    for i, entry in enumerate(entries, 1):
        start = _format_srt_time(entry['start'])
        end = _format_srt_time(entry['end'])
        text = entry['text'].replace('\n', '\n')
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(f"{text}")
        lines.append("")  # 空行分隔
    return '\n'.join(lines)


def to_vtt(entries):
    """将字幕条目列表转换为 WebVTT 格式文本"""
    lines = ["WEBVTT", ""]
    for i, entry in enumerate(entries, 1):
        start = _format_vtt_time(entry['start'])
        end = _format_vtt_time(entry['end'])
        text = entry['text']
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(f"{text}")
        lines.append("")
    return '\n'.join(lines)


def to_ass(entries, title="Converted Subtitles"):
    """将字幕条目列表转换为 ASS 格式文本"""
    lines = []
    # Script Info
    lines.append("[Script Info]")
    lines.append(f"Title: {title}")
    lines.append("ScriptType: v4.00+")
    lines.append("WrapStyle: 0")
    lines.append("ScaledBorderAndShadow: yes")
    lines.append("YCbCr Matrix: None")
    lines.append("PlayResX: 1920")
    lines.append("PlayResY: 1080")
    lines.append("")

    # Styles
    lines.append("[V4+ Styles]")
    lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                 "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                 "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                 "Alignment, MarginL, MarginR, MarginV, Encoding")
    lines.append("Style: Default,Microsoft YaHei,48,&H00FFFFFF,&H000000FF,"
                 "&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1")
    lines.append("")

    # Events
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    for entry in entries:
        start = _format_ass_time(entry['start'])
        end = _format_ass_time(entry['end'])
        # 将换行替换为 ASS 的 \N
        text = entry['text'].replace('\n', '\\N')
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return '\n'.join(lines)


def detect_subtitle_format(file_path):
    """
    根据文件扩展名和内容特征检测字幕格式。

    Args:
        file_path: 文件路径

    Returns:
        str: 'srt' / 'vtt' / 'ass' / 'ssa' / None
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.srt':
        return 'srt'
    elif ext == '.vtt':
        return 'vtt'
    elif ext == '.ass':
        return 'ass'
    elif ext == '.ssa':
        return 'ssa'
    return None


def parse_subtitle(file_path):
    """
    根据文件类型自动解析字幕文件。

    Args:
        file_path: 字幕文件路径

    Returns:
        tuple: (entries: list[dict], format: str, raw_content: str)
    """
    fmt = detect_subtitle_format(file_path)
    if not fmt:
        raise ValueError(f"不支持的字幕格式: {file_path}")

    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding) as f:
        content = f.read()

    if fmt == 'srt':
        entries = parse_srt(content)
    elif fmt == 'vtt':
        entries = parse_vtt(content)
    else:  # ass / ssa
        entries = parse_ass(content)

    return entries, fmt, content


# ============================================================================
# 多语言字典
# ============================================================================

LANG = {
    'zh': {
        'title': '字幕格式转换器',
        'drop_hint': '拖拽字幕文件到此处，或点击下方按钮选择',
        'browse': '选择文件',
        'clear': '清空列表',
        'batch': '批量转换',
        'target_format': '目标格式',
        'convert': '开始转换',
        'save_as': '另存为',
        'original': '原始内容',
        'converted': '转换结果',
        'no_file': '请先选择字幕文件',
        'convert_success': '转换完成',
        'saved': '已保存到',
        'unsupported': '不支持的文件格式',
        'parse_error': '解析失败',
        'preview_hint': '选择文件后此处显示预览',
        'file_list': '已选文件',
        'encoding_detected': '检测到编码',
        'stats': '统计',
        'entries_count': '字幕条数',
        'batch_result': '批量转换结果',
        'success': '成功',
        'failed': '失败',
        'lang_btn': 'EN',
        'all_files': '字幕文件',
        'file_filter': '字幕文件 (*.srt *.vtt *.ass *.ssa)|*.srt;*.vtt;*.ass;*.ssa',
        'format_srt': 'SRT 格式',
        'format_vtt': 'VTT 格式',
        'format_ass': 'ASS 格式',
        'select_files': '选择字幕文件',
        'ready': '就绪',
    },
    'en': {
        'title': 'Subtitle Converter',
        'drop_hint': 'Drop subtitle files here, or click button below',
        'browse': 'Browse',
        'clear': 'Clear List',
        'batch': 'Batch Convert',
        'target_format': 'Target Format',
        'convert': 'Convert',
        'save_as': 'Save As',
        'original': 'Original',
        'converted': 'Converted',
        'no_file': 'Please select subtitle files first',
        'convert_success': 'Conversion complete',
        'saved': 'Saved to',
        'unsupported': 'Unsupported file format',
        'parse_error': 'Parse error',
        'preview_hint': 'Preview will appear here after file selection',
        'file_list': 'Selected Files',
        'encoding_detected': 'Encoding detected',
        'stats': 'Statistics',
        'entries_count': 'Subtitle entries',
        'batch_result': 'Batch Conversion Result',
        'success': 'Success',
        'failed': 'Failed',
        'lang_btn': '中文',
        'all_files': 'Subtitle Files',
        'file_filter': 'Subtitle Files (*.srt *.vtt *.ass *.ssa)|*.srt;*.vtt;*.ass;*.ssa',
        'format_srt': 'SRT Format',
        'format_vtt': 'VTT Format',
        'format_ass': 'ASS Format',
        'select_files': 'Select Subtitle Files',
        'ready': 'Ready',
    }
}


# ============================================================================
# GUI 主类
# ============================================================================

class SubtitleConverterApp:
    """
    字幕格式转换器 GUI 应用主类。

    特性：
      - 文件拖拽 / 浏览选择
      - 批量转换
      - 预览对比（左右分栏）
      - 中英文双语切换
      - 全屏窗口
    """

    def __init__(self):
        """初始化 GUI 应用"""
        self.root = tk.Tk()
        self.root.title("字幕格式转换器 - Subtitle Converter")
        self.root.configure(bg='#f0fdfa')

        # 当前语言
        self.current_lang = 'zh'

        # 已选择的文件列表
        self.selected_files = []

        # 当前转换结果缓存（file_path -> converted_text）
        self.converted_cache = {}

        # 窗口全屏
        self.root.attributes('-fullscreen', True)

        # Esc 退出全屏
        self.root.bind('<Escape>', lambda e: self.root.attributes('-fullscreen', False))

        # 注册拖拽支持
        register_drop_target(self.root, self._on_files_dropped)

        # 构建界面
        self._build_ui()

    # --------------------------------------------------------------------------
    # 界面构建
    # --------------------------------------------------------------------------

    def _build_ui(self):
        """构建所有 GUI 组件"""
        # ---- 顶部标题栏 ----
        top_bar = tk.Frame(self.root, bg='#0d9488', height=48)
        top_bar.pack(fill=tk.X, side=tk.TOP)
        top_bar.pack_propagate(False)

        self.title_label = tk.Label(
            top_bar, text='字幕格式转换器',
            font=('Microsoft YaHei', 14, 'bold'),
            fg='#ffffff', bg='#0d9488'
        )
        self.title_label.pack(side=tk.LEFT, padx=20, pady=8)

        # 语言切换按钮
        self.lang_btn = tk.Button(
            top_bar, text='EN', font=('Microsoft YaHei', 10, 'bold'),
            fg='#0d9488', bg='#ffffff', relief='flat',
            activebackground='#f0fdfa', activeforeground='#0d9488',
            cursor='hand2', padx=12, pady=2,
            command=self._toggle_language
        )
        self.lang_btn.pack(side=tk.RIGHT, padx=16, pady=8)

        # ---- 主内容区域 ----
        main_frame = tk.Frame(self.root, bg='#f0fdfa')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 8))

        # -- 文件选择区域 --
        drop_frame = tk.Frame(main_frame, bg='#ffffff', relief='solid', bd=1)
        drop_frame.pack(fill=tk.X, pady=(0, 10))

        # 拖拽提示标签
        self.drop_label = tk.Label(
            drop_frame,
            text='拖拽字幕文件到此处，或点击下方按钮选择',
            font=('Microsoft YaHei', 11),
            fg='#94a3b8', bg='#ffffff',
            height=3
        )
        self.drop_label.pack(fill=tk.X, padx=12, pady=8)

        # 文件列表区
        self.file_listbox = tk.Listbox(
            main_frame, font=('Microsoft YaHei', 10),
            fg='#0f172a', bg='#ffffff',
            selectbackground='#0d9488', selectforeground='#ffffff',
            height=4, relief='solid', bd=1
        )
        self.file_listbox.pack(fill=tk.X, pady=(0, 8))

        # 按钮行
        btn_row = tk.Frame(main_frame, bg='#f0fdfa')
        btn_row.pack(fill=tk.X, pady=(0, 10))

        self.browse_btn = tk.Button(
            btn_row, text='选择文件',
            font=('Microsoft YaHei', 10),
            fg='#ffffff', bg='#0d9488', relief='flat',
            activebackground='#0f766e', activeforeground='#ffffff',
            cursor='hand2', padx=14, pady=4,
            command=self._browse_files
        )
        self.browse_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.clear_btn = tk.Button(
            btn_row, text='清空列表',
            font=('Microsoft YaHei', 10),
            fg='#0f172a', bg='#e2e8f0', relief='flat',
            activebackground='#cbd5e1', activeforeground='#0f172a',
            cursor='hand2', padx=14, pady=4,
            command=self._clear_files
        )
        self.clear_btn.pack(side=tk.LEFT)

        # -- 目标格式选择 --
        fmt_frame = tk.Frame(main_frame, bg='#f0fdfa')
        fmt_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            fmt_frame, text='目标格式',
            font=('Microsoft YaHei', 11, 'bold'),
            fg='#0f172a', bg='#f0fdfa'
        ).pack(side=tk.LEFT, padx=(0, 12))

        # 格式选择变量
        self.target_format = tk.StringVar(value='srt')

        formats = [
            ('SRT 格式', 'srt'),
            ('VTT 格式', 'vtt'),
            ('ASS 格式', 'ass'),
        ]

        for text, value in formats:
            self._fmt_radio = tk.Radiobutton(
                fmt_frame, text=text, variable=self.target_format,
                value=value, font=('Microsoft YaHei', 10),
                fg='#0f172a', bg='#f0fdfa',
                selectcolor='#f0fdfa',
                activebackground='#f0fdfa',
                command=self._on_format_changed
            )
            self._fmt_radio.pack(side=tk.LEFT, padx=(0, 16))

        # 转换按钮
        self.convert_btn = tk.Button(
            fmt_frame, text='开始转换',
            font=('Microsoft YaHei', 10, 'bold'),
            fg='#ffffff', bg='#0d9488', relief='flat',
            activebackground='#0f766e', activeforeground='#ffffff',
            cursor='hand2', padx=18, pady=5,
            command=self._convert
        )
        self.convert_btn.pack(side=tk.RIGHT)

        self.batch_var = tk.BooleanVar(value=False)
        self.batch_cb = tk.Checkbutton(
            fmt_frame, text='批量转换',
            variable=self.batch_var,
            font=('Microsoft YaHei', 10),
            fg='#0f172a', bg='#f0fdfa',
            selectcolor='#f0fdfa',
            activebackground='#f0fdfa'
        )
        self.batch_cb.pack(side=tk.RIGHT, padx=(0, 12))

        # -- 预览区域（左右分栏） --
        preview_frame = tk.Frame(main_frame, bg='#f0fdfa')
        preview_frame.pack(fill=tk.BOTH, expand=True)

        # 左栏：原始内容
        left_frame = tk.Frame(preview_frame, bg='#f0fdfa')
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        tk.Label(
            left_frame, text='原始内容',
            font=('Microsoft YaHei', 10, 'bold'),
            fg='#0f172a', bg='#f0fdfa'
        ).pack(anchor=tk.W)

        self.original_text = scrolledtext.ScrolledText(
            left_frame,
            font=('Consolas', 10),
            fg='#0f172a', bg='#ffffff',
            relief='solid', bd=1,
            wrap=tk.WORD
        )
        self.original_text.pack(fill=tk.BOTH, expand=True)
        self.original_text.insert(tk.END, '选择文件后此处显示预览')
        self.original_text.config(state=tk.DISABLED)

        # 右栏：转换结果
        right_frame = tk.Frame(preview_frame, bg='#f0fdfa')
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 0))

        tk.Label(
            right_frame, text='转换结果',
            font=('Microsoft YaHei', 10, 'bold'),
            fg='#0f172a', bg='#f0fdfa'
        ).pack(anchor=tk.W)

        self.converted_text = scrolledtext.ScrolledText(
            right_frame,
            font=('Consolas', 10),
            fg='#0f172a', bg='#ffffff',
            relief='solid', bd=1,
            wrap=tk.WORD
        )
        self.converted_text.pack(fill=tk.BOTH, expand=True)
        self.converted_text.insert(tk.END, '选择文件后此处显示预览')
        self.converted_text.config(state=tk.DISABLED)

        # ---- 底部状态栏 ----
        self.status_bar = tk.Label(
            self.root, text='就绪',
            font=('Microsoft YaHei', 9),
            fg='#64748b', bg='#e2e8f0',
            anchor=tk.W, padx=12, pady=3
        )
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # --------------------------------------------------------------------------
    # 语言切换
    # --------------------------------------------------------------------------

    def _toggle_language(self):
        """切换中英文界面语言"""
        self.current_lang = 'en' if self.current_lang == 'zh' else 'zh'
        self._refresh_language()

    def _t(self, key):
        """获取当前语言的文本"""
        return LANG[self.current_lang].get(key, key)

    def _refresh_language(self):
        """刷新所有界面文本"""
        lang = LANG[self.current_lang]

        self.title_label.config(text=lang['title'])
        self.lang_btn.config(text=lang['lang_btn'])
        self.drop_label.config(text=lang['drop_hint'])
        self.browse_btn.config(text=lang['browse'])
        self.clear_btn.config(text=lang['clear'])
        self.convert_btn.config(text=lang['convert'])
        self.batch_cb.config(text=lang['batch'])
        self.status_bar.config(text=lang['ready'])

        # 更新格式标签
        for child in self.convert_btn.master.winfo_children():
            if isinstance(child, tk.Radiobutton):
                val = child.cget('value')
                key_map = {'srt': 'format_srt', 'vtt': 'format_vtt', 'ass': 'format_ass'}
                if val in key_map:
                    child.config(text=lang[key_map[val]])

        # 更新预览区标签
        for frame in self.original_text.master.master.winfo_children():
            if isinstance(frame, tk.Frame):
                for child in frame.winfo_children():
                    if isinstance(child, tk.Label):
                        if child.cget('text') in ('原始内容', 'Original'):
                            child.config(text=lang['original'])
                        elif child.cget('text') in ('转换结果', 'Converted'):
                            child.config(text=lang['converted'])
                    elif isinstance(child, tk.Frame):
                        for sub in child.winfo_children():
                            if isinstance(sub, tk.Label):
                                if sub.cget('text') in ('原始内容', 'Original'):
                                    sub.config(text=lang['original'])
                                elif sub.cget('text') in ('转换结果', 'Converted'):
                                    sub.config(text=lang['converted'])

    # --------------------------------------------------------------------------
    # 文件选择与拖拽
    # --------------------------------------------------------------------------

    def _on_files_dropped(self, file_paths):
        """处理拖拽放入的文件"""
        subtitle_exts = {'.srt', '.vtt', '.ass', '.ssa'}
        valid_files = [f for f in file_paths if os.path.splitext(f)[1].lower() in subtitle_exts]

        if not valid_files:
            messagebox.showwarning(
                self._t('title'),
                self._t('unsupported') + ': ' + ', '.join(file_paths)
            )
            return

        for f in valid_files:
            if f not in self.selected_files:
                self.selected_files.append(f)
                self.file_listbox.insert(tk.END, f)

        self._update_preview_for_first()

    def _browse_files(self):
        """浏览并选择字幕文件"""
        file_paths = filedialog.askopenfilenames(
            title=self._t('select_files'),
            filetypes=[
                (self._t('all_files'), '*.srt *.vtt *.ass *.ssa'),
                ('SRT', '*.srt'),
                ('VTT', '*.vtt'),
                ('ASS/SSA', '*.ass *.ssa'),
            ]
        )
        if file_paths:
            for f in file_paths:
                if f not in self.selected_files:
                    self.selected_files.append(f)
                    self.file_listbox.insert(tk.END, f)
            self._update_preview_for_first()

    def _clear_files(self):
        """清空已选文件列表"""
        self.selected_files.clear()
        self.converted_cache.clear()
        self.file_listbox.delete(0, tk.END)
        self._clear_preview()
        self.status_bar.config(text=self._t('ready'))

    def _on_format_changed(self):
        """目标格式变更时更新预览"""
        if self.selected_files:
            self._update_preview_for_first()

    # --------------------------------------------------------------------------
    # 预览更新
    # --------------------------------------------------------------------------

    def _update_preview_for_first(self):
        """更新第一个文件的预览"""
        if not self.selected_files:
            return

        file_path = self.selected_files[0]
        try:
            entries, src_fmt, raw_content = parse_subtitle(file_path)
            target_fmt = self.target_format.get()

            # 更新原始内容
            self._set_text(self.original_text, raw_content)

            # 转换并显示
            converted = self._convert_entries(entries, target_fmt)
            self._set_text(self.converted_text, converted)

            # 缓存
            self.converted_cache[file_path] = converted

            encoding = detect_encoding(file_path)
            self.status_bar.config(
                text=f"{self._t('encoding_detected')}: {encoding} | "
                     f"{self._t('entries_count')}: {len(entries)}"
            )
        except Exception as e:
            self._set_text(self.original_text, f"{self._t('parse_error')}: {e}")
            self._set_text(self.converted_text, '')
            self.status_bar.config(text=f"{self._t('parse_error')}: {e}")

    def _set_text(self, widget, text):
        """设置文本框内容（只读模式切换）"""
        widget.config(state=tk.NORMAL)
        widget.delete('1.0', tk.END)
        widget.insert(tk.END, text)
        widget.config(state=tk.DISABLED)

    def _clear_preview(self):
        """清空预览区域"""
        self._set_text(self.original_text, self._t('preview_hint'))
        self._set_text(self.converted_text, self._t('preview_hint'))

    # --------------------------------------------------------------------------
    # 转换逻辑
    # --------------------------------------------------------------------------

    def _convert_entries(self, entries, target_fmt):
        """根据目标格式转换字幕条目"""
        if target_fmt == 'srt':
            return to_srt(entries)
        elif target_fmt == 'vtt':
            return to_vtt(entries)
        elif target_fmt == 'ass':
            return to_ass(entries)
        else:
            raise ValueError(f"未知的目标格式: {target_fmt}")

    def _get_output_path(self, input_path, target_fmt):
        """生成输出文件路径（原目录 + 原文件名 + 格式后缀）"""
        dir_name = os.path.dirname(input_path)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        ext_map = {'srt': '.srt', 'vtt': '.vtt', 'ass': '.ass'}
        new_ext = ext_map.get(target_fmt, '.txt')

        # 避免覆盖：如果同名文件已存在，添加序号
        output_path = os.path.join(dir_name, f"{base_name}_{target_fmt}{new_ext}")
        counter = 1
        while os.path.exists(output_path):
            output_path = os.path.join(dir_name, f"{base_name}_{target_fmt}_{counter}{new_ext}")
            counter += 1
        return output_path

    def _convert(self):
        """执行转换操作"""
        if not self.selected_files:
            messagebox.showwarning(self._t('title'), self._t('no_file'))
            return

        target_fmt = self.target_format.get()
        results = []
        errors = []

        files_to_process = self.selected_files if self.batch_var.get() else [self.selected_files[0]]

        for file_path in files_to_process:
            try:
                entries, src_fmt, raw_content = parse_subtitle(file_path)

                # 检查是否同格式
                if src_fmt == target_fmt:
                    errors.append((file_path, f"源格式已是 {target_fmt.upper()}，无需转换"))
                    continue

                converted = self._convert_entries(entries, target_fmt)

                # 保存文件（UTF-8 编码）
                output_path = self._get_output_path(file_path, target_fmt)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(converted)

                results.append((file_path, output_path, len(entries)))
                self.converted_cache[file_path] = converted

            except Exception as e:
                errors.append((file_path, str(e)))

        # 更新预览
        if self.selected_files:
            self._update_preview_for_first()

        # 显示结果
        lang = LANG[self.current_lang]
        msg_parts = []

        if results:
            msg_parts.append(f"{lang['success']}: {len(results)}")
            for src, dst, count in results:
                msg_parts.append(f"  {os.path.basename(src)} → {os.path.basename(dst)} ({count} {lang['entries_count']})")

        if errors:
            msg_parts.append(f"\n{lang['failed']}: {len(errors)}")
            for src, err in errors:
                msg_parts.append(f"  {os.path.basename(src)}: {err}")

        if results and not errors:
            self.status_bar.config(text=f"{lang['convert_success']} - {len(results)} {lang['success']}")
        elif results and errors:
            self.status_bar.config(text=f"{len(results)} {lang['success']}, {len(errors)} {lang['failed']}")
        else:
            self.status_bar.config(text=lang['failed'])

        messagebox.showinfo(lang['title'], '\n'.join(msg_parts))

    # --------------------------------------------------------------------------
    # 运行
    # --------------------------------------------------------------------------

    def run(self):
        """启动 GUI 主循环"""
        self.root.mainloop()


# ============================================================================
# 入口
# ============================================================================

if __name__ == '__main__':
    app = SubtitleConverterApp()
    app.run()
