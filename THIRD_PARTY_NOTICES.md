# 第三方素材与许可

本项目**代码**以 [MIT License](LICENSE) 发布。下面这些是从别处来的东西，版权归各自作者，
不因为我把它打包进这个项目就变成我的。分发时请连同本文件一起带上。

---

## 1. 桌宠立绘 `pet/assets/whale_front.png`、`whale_side.png`

| 项 | 内容 |
|---|---|
| 来源 | https://github.com/1190fasheqi/dafeiyu-pet （`sprites/正面.png`、`sprites/侧面.png`） |
| 作者 | 1190fasheqi 及该仓库 README 标注的原始画师 |
| 授权 | **MIT License**，见上游仓库的 `LICENSE` |
| 性质 | DeepSeek 鲸鱼形象的**二创**桌宠素材，**非官方**，与 DeepSeek 公司无关 |
| 本项目用法 | 只把立绘当桌宠形象用，未使用其代码 |

MIT 允许修改与再分发，但**必须保留版权与许可声明**。
上游 LICENSE 原文：https://github.com/1190fasheqi/dafeiyu-pet/blob/main/LICENSE

> ⚠️ 商用前请自行确认：DeepSeek 的鲸鱼形象本身可能涉及商标/形象权，
> MIT 只覆盖上游仓库作者的代码与美术贡献，不覆盖角色形象本身。
> 想换成完全自有的形象，见 `pet/assets/NOTICE.md` 的「换形象」一节。

---

## 2. 手部识别模型 `models/*.task`、`models/*.tflite`

| 项 | 内容 |
|---|---|
| 来源 | MediaPipe 官方发布的模型文件（Google） |
| 授权 | **Apache License 2.0** |
| 文件 | `hand_landmarker.task`、`gesture_recognizer.task`、`blaze_face_short_range.tflite`、`blaze_face_full_range.tflite` |
| 说明 | 模型随仓库分发只为免去使用者自己下载；未做任何修改 |

Apache-2.0 全文：https://www.apache.org/licenses/LICENSE-2.0

---

## 3. Python 依赖

运行依赖（`requirements.txt` 里的 PyQt6、MediaPipe、OpenCV、NumPy、pywin32 等）**不在本仓库内**，
由 `setup.bat` 首次运行时通过 pip 安装。它们各自的许可随包分发，与本项目的 MIT 无关。
其中 **PyQt6 是 GPL-3.0 / 商业双许可**——如果你要把本项目**闭源分发**，
PyQt6 这一条需要单独处理（换成 PySide6 走 LGPL，或购买 Riverbank 的商业许可）。
源码公开、自己用、内部用都不受影响。
