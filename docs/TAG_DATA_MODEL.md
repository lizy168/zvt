# Tag 模块数据模型梳理

本文档梳理 tag 相关 schema（`tag_schemas` + `tag_models`）的现状，并对照「股票属性：行业 / 概念 / 地区、主标签 / 次标签 / 隐藏标签」及关系，标出**定义不清晰**和**缺失**的部分。

---

## 一、当前模型总览

### 1. 股票上的“标签”存储：`StockTags`

| 字段 | 含义（当前） | 说明 |
|------|--------------|------|
| `entity_type` / `entity_id` | 标的 | 股票等 |
| `main_tag` | 当前主标签 | 单一，用于展示/筛选 |
| `main_tag_reason` | 主标签理由 | |
| `main_tags` | 主标签集合 | JSON `{ tag: reason }`，历史/备选 |
| `sub_tag` | 当前次标签 | 单一 |
| `sub_tag_reason` | 次标签理由 | |
| `sub_tags` | 次标签集合 | JSON `{ tag: reason }` |
| `active_hidden_tags` | 当前选中的隐藏标签 | JSON `{ tag: reason }` |
| `hidden_tags` | 隐藏标签集合 | JSON `{ tag: reason }` |
| `set_by_user` | 是否用户手动设置 | |

- **行业、概念、地区** 没有在 `StockTags` 上单独成字段，而是通过「行业/概念 → 主标签/次标签」的映射间接参与。

### 2. 主/次/隐藏标签的“字典表”

| Schema | 表 | 含义 | 关键字段 |
|--------|-----|------|----------|
| `MainTagInfo` | main_tag_info | 主标签字典 | tag, tag_reason |
| `SubTagInfo` | sub_tag_info | 次标签字典 + **归属主标签** | tag, tag_reason, **main_tag** |
| `HiddenTagInfo` | hidden_tag_info | 隐藏标签字典 | tag, tag_reason |

- **主标签 ↔ 次标签**：在 `SubTagInfo` 里用 `main_tag` 表示「该次标签属于哪个主标签」，即「一个主标签包含多个次标签」是在这里体现的。

### 3. 行业与主标签的映射：`IndustryInfo`

| 字段 | 含义 |
|------|------|
| industry_name | 行业名称（单一字符串） |
| description | 描述 |
| main_tag | 该行业对应的主标签 |

- 数据来源：`Block(category=industry)` 的 name + 配置文件 `industry_main_tag_mapping.json` 等。
- **问题**：只有一层 `industry_name`，没有一级/二级/三级行业层级。

### 4. 概念与主标签的关系（当前实现）

- **概念** 在 domain 里是 `Block(category=concept)` 的 name。
- 在 tag 体系里**没有**单独的「概念字典表」，而是：
  - **概念名 = 次标签名**：概念与次标签共用 `SubTagInfo`，`SubTagInfo.tag` 既表示「次标签」也表示「概念名」。
  - **概念 → 主标签**：通过 `SubTagInfo.main_tag` 表示，即「该概念(次标签)属于哪个主标签」。
- 因此「行业、概念对应的主标签」的关系是：
  - 行业 → 主标签：`IndustryInfo.main_tag`
  - 概念 → 主标签：`SubTagInfo.main_tag`（概念即次标签）

### 5. 地区（区域）

- **Domain**：`BlockCategory.area` 存在，`StockDetail.area_indices` 存在（所属区域相关）。
- **Tag 体系**：**没有任何 schema** 描述「地区」或「地区与标签的关系」，即**未定义**。

---

## 二、按你给的“股票属性”逐项对照

### 1. 行业（1/2/3 级）—— 定义不清晰 / 未体现层级

- **现状**：
  - 行业在 tag 里只通过 `IndustryInfo.industry_name`（单字符串）映射到 `main_tag`。
  - 行业数据源是 `Block(category=industry)`，当前 Block 是扁平的，没有 level 或 parent。
  - `StockDetail` 有 `industries`（字符串），也未区分 1/2/3 级。
- **不清晰/缺失**：
  - 没有「一级行业、二级行业、三级行业」的层级定义。
  - 没有说明 `industry_name` 对应的是哪一级，以及各级之间如何关联、如何参与主标签映射（按哪一级映射等）。

**建议**（若需要 1/2/3 级）：  
- 要么在 tag 体系内增加行业层级（例如 `industry_level`、`parent_industry_id` 或等价结构），  
- 要么在文档和字段注释里明确：当前仅支持「单层行业名」到主标签的映射，且 `industry_name` 对应的是哪一级（例如统一用二级）。

---

### 2. 概念 —— 与“次标签”混用，定义不清晰

- **现状**：
  - 概念 = `Block(category=concept)` 的 name。
  - 在 tag 里概念和「次标签」共用 `SubTagInfo`：同一个 tag 既是概念名，也是次标签名。
  - 股票上的「当前概念/次标签」在 `StockTags.sub_tag` / `StockTags.sub_tags` 里。
- **不清晰**：
  - **概念** 与 **次标签** 在模型上没有区分：一个 tag 到底是「来自概念板块的概念」还是「人为定义的次标签」无法从 schema 区分。
  - 若未来概念列表（Block 拉取）与运营维护的「次标签」列表不完全一致，当前设计会难以扩展（例如：仅部分概念参与主标签映射、部分仅作展示等）。

**建议**：  
- 在文档中明确：当前约定「概念 = 次标签」，概念名即 `SubTagInfo.tag`。  
- 若业务上需要区分「概念」与「次标签」，可考虑：  
  - 增加 `ConceptInfo`（概念字典 + concept → main_tag），与 `SubTagInfo` 分离；或  
  - 在 `SubTagInfo` 上增加来源字段（如 `source: concept | manual`）。

---

### 3. 地区 —— 未定义

- **现状**：
  - Domain 有 `BlockCategory.area`、`StockDetail.area_indices`。
  - Tag 的 `tag_schemas` / `tag_models` 中**没有任何**「地区」或「区域」的表、字段、API 模型。
- **缺失**：  
  - 地区是否有对应「标签」？  
  - 是否也需要「地区 → 主标签」或「地区 → 某类标签」的映射？  
  - 若需要，应增加类似 `RegionInfo` 或 `AreaInfo` 的 schema 及与主标签的关系定义。

---

### 4. 主标签 / 次标签 / 隐藏标签 —— 定义相对清晰，有少量不清晰

- **主标签**：`MainTagInfo` + `StockTags.main_tag` / `main_tags`，含义清晰。
- **次标签**：`SubTagInfo`（含 `main_tag`）+ `StockTags.sub_tag` / `sub_tags`；与概念的混用见上。
- **隐藏标签**：`HiddenTagInfo` + `StockTags.active_hidden_tags` / `hidden_tags`，含义清晰。

不清晰点：
- **主/次/隐藏** 的「业务含义」和「使用场景」在 schema 和注释里没有简短说明（例如：主标签=主线板块，次标签=概念/细分，隐藏标签=筛选用属性），不利于后续扩展和接口设计。
- `TagInfoModel`（tag_models）里有个 `main_tag: Optional[str]`，仅对 `SubTagInfo` 的返回有意义，对 `MainTagInfo`/`HiddenTagInfo` 会为 None，容易让人误解「所有 tag 都有 main_tag」。

---

### 5. 「行业、概念对应的主标签」关系 —— 分散且命名不统一

- **行业 → 主标签**：`IndustryInfo.main_tag`，表意清晰。
- **概念 → 主标签**：`SubTagInfo.main_tag`（概念即次标签），没有单独的「概念→主标签」表，关系隐藏在「次标签」里，命名上不够直观。

若希望「行业、概念」在模型上对称，可考虑：  
- 要么在文档中明确写清「概念→主标签 = SubTagInfo.main_tag」；  
- 要么增加 `ConceptMainTagRelation` 或类似结构（可与 `IndustryInfo` 对称），并在实现上从 `SubTagInfo` 或 Block 同步/派生。

---

### 6. 「一个主标签包含多个次标签」—— 已体现，但可更显式

- **现状**：通过 `SubTagInfo` 多条记录共享同一 `main_tag` 表示，查询时按 `main_tag` 聚合即可。
- **可改进**：  
  - 在 `MainTagInfo` 或单独关系表上不做强制，但可在文档/注释里明确写清：**主标签 : 次标签 = 1 : N，关系存储在 SubTagInfo.main_tag**。  
  - 若希望「主标签」侧也有显式列表，可增加「主标签 ↔ 次标签列表」的只读视图或 API，而不一定改表结构。

---

## 三、其他定义不清晰点（与 tag_schemas 相关）

1. **StockTags 与实体（股票）的对应关系**  
   - 当前是「一股票一当前快照」还是「一股票多历史快照」？从 `order=StockTags.timestamp.desc()` 和 `limit=1` 的用法看像是「取最新一条」；若如此，是否考虑用「唯一约束 (entity_id) + 覆盖写」或明确「仅保留最新」的语义并写在 schema 注释里。

2. **main_tags / sub_tags / hidden_tags 的语义**  
   - 注释只写「JSON 字典」，未说明是「该股票曾用过的所有主/次/隐藏标签」还是「当前可选项」等，建议在 `StockTags` 或文档中写清。

3. **IndustryInfo 与 Block 的同步关系**  
   - `IndustryInfo` 的 `industry_name` 与 `Block(category=industry).name` 是否一一对应、由谁维护、是否支持「仅部分行业参与主标签映射」未在 schema 或文档说明。

4. **entity_type 在 StockTags 中的取值**  
   - 仅有 `entity_type` 字段，没有枚举或注释说明允许的值（如 stock / stock_hk / stock_us），不利于校验和文档生成。

---

## 四、总结表（哪些定义不够清晰）

| 属性/关系 | 现状 | 问题 |
|-----------|------|------|
| 行业 | IndustryInfo 单层 industry_name → main_tag | 无 1/2/3 级层级；未说明对应哪一级 |
| 概念 | 与次标签共用 SubTagInfo | 概念与次标签未区分；概念→主标签不直观 |
| 地区 | 无 | 完全未定义 |
| 主标签 | MainTagInfo + StockTags | 业务含义未在 schema 说明 |
| 次标签 | SubTagInfo + StockTags | 与概念混用；业务含义未说明 |
| 隐藏标签 | HiddenTagInfo + StockTags | 业务含义未说明 |
| 行业→主标签 | IndustryInfo.main_tag | 清晰 |
| 概念→主标签 | SubTagInfo.main_tag | 藏在「次标签」里，不直观 |
| 主标签↔次标签 | SubTagInfo.main_tag | 已体现 1:N，可文档化更显式 |

若要下一步做「模型改进」，优先建议：  
1）明确行业层级策略（或声明当前仅单层）；  
2）在文档或 schema 中区分「概念」与「次标签」的约定或加字段；  
3）补充「地区」是否进入 tag 体系及若进入时的表结构。
