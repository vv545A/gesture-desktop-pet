# 素材来源与授权

桌宠形象换过一次，两套素材都留在仓库里。当前默认用的是**第 1 套（小桃）**，
第 2 套（鲸鱼娘）保留兼容 —— 配置里指过去就能换回来。

---

## 1. `girl/*.png` —— 「小桃」七帧立绘（**当前默认**）

| 项 | 内容 |
|---|---|
| 文件 | `stand.png` `happy.png` `wave.png` `think.png` `sleepy.png` `angry.png` `sit.png` |
| 来源 | 本项目自有素材：作者提供参考图，用 AI 生图工具（豆包）生成后处理 |
| 处理 | 由 `tools/prep_sprites.py` 自动抠背景 → 统一包围盒裁剪 → 缩放对齐 |
| 授权 | 随本项目一起，按仓库根目录 `LICENSE`（MIT）发布 |

七张图**共用一套包围盒**（都是 386×580），所以换帧时人物不会左右横跳 ——
这是抠图脚本刻意做的，改素材时别单独裁剪某一张。

> 如果要商用：AI 生图工具的商用条款请自行确认；参考图的形象权也请自行确认。
> 最省事的做法是换成你自己画的图（见最后一节）。

### 帧名约定（很重要）

**文件名就是帧名。** `SpriteSet` 会把 `sprite` 所在目录下所有 PNG 当成帧加载，
`pet/ui/anim.py` 的 `MOOD_FRAME` 决定"什么情绪用哪张"：

| 帧名 | 什么时候用 |
|---|---|
| `stand` | 默认/待机、听到声音 |
| `happy` | 开心、得意、被摸 |
| `wave` | 招手、惊讶（举手张嘴，正好是"欸？！"） |
| `think` | 思考、害羞（抱胸闭眼脸红，比笑更像害羞） |
| `sleepy` | 困了、打哈欠 |
| `angry` | 生气、被戳烦了 |
| `sit` | 久无人理，坐下 |

少几张也能跑：查不到的帧会回退到 `sprite` 指的那张（兜底帧）。

---

## 2. `whale_front.png` / `whale_side.png` —— 鲸鱼娘「大肥鱼」（上一版）

| 项 | 内容 |
|---|---|
| 来源 | https://github.com/1190fasheqi/dafeiyu-pet 的 `sprites/正面.png`、`sprites/侧面.png` |
| 作者 | 1190fasheqi（及该仓库 README 中标注的原始画师） |
| 授权 | **MIT License**（见上游仓库 `LICENSE`） |
| 性质 | DeepSeek 鲸鱼形象的**二创**桌宠素材，**非官方**，与 DeepSeek 公司无关 |
| 本项目用法 | 仅取立绘当桌面宠物形象，未使用其代码 |

MIT 允许修改与再分发，但**必须保留版权与许可声明**。如果要发布这个项目，
请把本文件与上游的 `LICENSE` 一起打包；上游 LICENSE 原文：
https://github.com/1190fasheqi/dafeiyu-pet/blob/main/LICENSE

> 商用前请自行确认：DeepSeek 的鲸鱼形象本身可能涉及商标/形象权，
> MIT 只覆盖该仓库作者的代码与美术贡献。

想换回旧形象：

```json
"sprite": "assets/whale_front.png",
"sprite_side": "assets/whale_side.png",
"pet_name": "小鲸",
"pet_scale": 1.0
```

（注意：这套只有正面和侧面两张，没有多帧，动作只剩位移和缩放。）

---

## 换形象（换成别的立绘 / 换成自己的画）

### A. 只有一张图（最简单）

1. 准备一张**背景透明**的 PNG（建议竖构图，高度 ≥ 300px）；
2. 放到 `pet/assets/`，例如 `pet/assets/my_pet.png`；
3. 改 `pet_config.json`：

```json
"sprite": "assets/my_pet.png",
"sprite_side": "",
"sprite_walk": false
```

- `sprite` 留空 `""` → 退回程序化矢量版（`pet/ui/paint.py` 里画的那只）
- `sprite_side` 是"走动时的侧面图"，没有就留空
- 图片比例不用管，`SpriteSet` 会按高度等比缩放（宽 = 高 × 原图宽高比）

### B. 有一组表情图（推荐，动作更丰富）

1. 把图**按帧名命名**（`stand.png` / `happy.png` / `wave.png` ……），放进同一个文件夹；
2. 指过去就行：

```json
"sprite": "assets/my_pet/stand.png",
"sprite_frames": ""
```

同目录下所有 PNG 都会变成可用帧，情绪会自动挑对应的那张。

### C. 图是"白底/浅底"的，不会抠背景

用 `tools/prep_sprites.py`。它做四件事：抠掉背景 / 去掉描边外的投影 /
把七张图裁到**同一个包围盒** / 统一缩放到同一高度。

```bat
python tools/prep_sprites.py --src "路径\我的素材" --dst pet/assets/my_pet
```

- 先把开头的 `FRAMES` 改成"你的文件名 → 帧名"（例如 `"01.png": "stand"`），
  输出名只用 ASCII，跨平台打包才不会出岔子；
- 如果背景不是奶油色 / 白描边不是白色，可能要调 `PASSABLE`（多亮算背景）；
- 出图后验一眼：`python -m pet --snapshot check.png`（一帧一格）。
