# 素材来源与授权

## whale_front.png / whale_side.png —— DeepSeek 鲸鱼娘「大肥鱼」立绘

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

## 换形象（换成别的立绘 / 换成自己的画）

1. 准备一张**背景透明**的 PNG（建议竖构图，高度 ≥ 300px）；
2. 放到 `pet/assets/`，例如 `pet/assets/my_pet.png`；
3. 改 `pet_config.json`：

```json
"sprite": "assets/my_pet.png",
"sprite_side": "",
"sprite_walk": false
```

- `sprite` 留空 `""` → 退回程序化矢量版（`pet/ui/paint.py`，自己画的鲸鱼娘）
- `sprite_side` 是"走动时的侧面图"，没有就留空，正面图会一直用
- 图片比例不用管，`SpriteSet` 会按高度等比缩放（宽 = 高 × 原图宽高比）
