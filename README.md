# 字幕格式转换器 (Subtitle Converter)

SRT / VTT / ASS(SSA) 三种字幕格式互相转换。

## 功能

- 拖拽或浏览选择文件，支持批量转换
- 转换前可预览原始内容与结果对比
- 编码自动检测：UTF-8 / UTF-8-BOM / GBK / UTF-16
- 内置中英文双语切换

## 运行

```bash
python subtitle_converter.py
```

零外部依赖，仅需 Python 标准库 + tkinter。

## 测试数据

`tests/` 目录下有 `test_ass.ass`、`test_srt.srt`、`test_vtt.vtt` 三个样例文件，
可直接用于验证转换效果。

## 注意事项

- ASS 字幕的样式、特效、定位信息在转换为 SRT/VTT 时会丢失（后两者不支持）
- ASS → SRT 时会剥离所有 `{}` 特效标签
