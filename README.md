# DiskScope · C 盘空间监控与增长归因

给"空间莫名其妙就满了"这类问题做定量归因：定期把所有目录的容量存成快照，
下次空间变大时，直接算出**是哪个目录、哪个文件涨了多少**。

不需要安装任何第三方库，只用 Python 标准库（3.9+，Windows）。

---

## 一、怎么用（三步）

| 步骤 | 操作 | 说明 |
|---|---|---|
| 1 | 双击 **`立即扫描并生成报告.bat`** | 首次会弹 UAC 提权。全盘扫描约 2 分钟，随后自动用浏览器打开报告 |
| 2 | 双击 **`安装定时任务.bat`** | 装一个每 6 小时以 SYSTEM 身份扫描的计划任务，快照自动累积 |
| 3 | 空间又变少了，双击 **`立即扫描并生成报告.bat`** 或 **`生成报告.bat`** | 报告第一屏就是"增长归因"排行 |

双击 **`启动面板.bat`** 有交互菜单（扫描 / 报告 / 控制台看增长 TOP / 实时分析某目录 / 装卸载任务 / 清理旧快照）。

> 第一次扫描后只有一个快照，还看不出"增长"。第二次扫描之后对比才有意义。
> 所以**先装定时任务**是最省心的用法。

---

## 二、报告里该看哪一块

1. **增长归因** —— 最重要。「自身增长」= 该节点总增长 − 直接子项总增长，
   即无法再向下拆解的净增量。排第一的那一行就是元凶，点进去逐层下钻能看到具体文件。
2. **目录变化明细** —— 全部节点的基线/当前/变化/归因，可按字段排序、按关键字过滤。
3. **常见占用元凶体检** —— 自动比对 30 个已知热点（休眠文件、WinSxS、更新缓存、
   卷影副本、WSL 虚拟磁盘、浏览器/IDE 缓存、pip/npm 缓存……），给出当前大小和处置建议。
4. **目录扫描覆盖不到的地方** —— 卷影副本、回收站等拿不到准确值的项目，
   附直接可粘贴的排查/清理命令。这也是"目录加起来对不上已用空间"的常见原因。

---

## 三、设计要点（为什么这么做）

### 1. 一次遍历得到全部层级
用显式栈迭代 `os.scandir`，每遇到一个文件就把字节数累加到**它自己 + 所有祖先目录**
（祖先链随栈传递）。因此只存深度 ≤ `track_depth` 的目录节点，却仍然得到每一层的真实容量。
全盘 74 万文件 / 27 万目录约 2 分钟，DB 只占几 MB。

### 2. 跳过重解析点（junction / 符号链接）
`C:\Users\All Users`、`C:\Documents and Settings`、`C:\Windows\WinSxS\*` 等
都是 reparse point。跟随会重复计算甚至死循环，所以按 `FILE_ATTRIBUTE_REPARSE_POINT` 整枝跳过。

### 3. 硬链接去重
WinSxS、Installer 里有数万个硬链接。不去重会凭空多算十几 GB，
导致"目录总量"和资源管理器对不上。按 `(st_dev, st_ino)` 去重（仅对 `st_nlink > 1` 的文件建索引）。

> 注意：`os.DirEntry.stat()` 在 Windows 上不填 `st_nlink / st_ino`（恒为 0），
> 必须显式 `os.stat()` 才能拿到链接数。代价是全盘扫描慢 3~6 倍（实测 25s → 136s，
> 换来 6.4% 的精度）。不想要这个开销就用 `scan --fast`，或把配置里
> `dedupe_hardlinks` 设为 false。

### 4. 长路径
统一加 `\\?\` 前缀，避免超过 260 字符的路径直接扫描失败。

### 5. 权限
读取 `System Volume Information`、其他用户目录等必须管理员。
工具检测到非管理员会通过 `ShellExecuteW("runas")` 自动重启到提权窗口；
计划任务以 `S-1-5-18`（SYSTEM）身份运行，既最高权限又不需要保存密码。

### 6. 存储
SQLite 两张表：`snapshot`（每次扫描一行）+ `node`（每个路径一行，主键 `(snapshot_id, path)`，
`WITHOUT ROWID`）。差值分析就是两个快照的 path 集合做 join，几万行毫秒级完成。

### 7. 增长归因
```
自身增长 = 目录总增长 − Σ(直接子目录与已记录大文件的增长)
```
只看总增长会出现父子重复（`C:\Users\x` 涨 5GB，其中 5GB 全来自 `AppData`，两行都显示 5GB）。
扣掉子项后剩下的才是"长在这个目录自己身上"的增量，排序即可定位真正的元凶。

---

## 四、命令行

```bat
python run.py menu                              交互菜单
python run.py scan                             扫描默认根目录（C:\）并存快照
python run.py scan --root D:\ --depth 6        换根目录/深度
python run.py scan --fast                      跳过硬链接去重，快 3~6 倍
python run.py scan --report --open             扫完立即出报告
python run.py diff --hours 72 --top 30         对比 72 小时前的快照
python run.py diff --base-id 3 --id 9          指定任意两个快照对比
python run.py report --open                    用最新快照出报告
python run.py top --path D:\data --depth 4     实时分析某目录容量分布（不写库）
python run.py list                             快照列表
python run.py watch --interval-min 30          常驻监视，超过阈值告警
python run.py install-task --every-hours 6     安装计划任务
python run.py install-task --at 12:30          每天 12:30
python run.py uninstall-task                   卸载
python run.py prune --keep 50                  只保留最近 50 个快照
```

安装后想确认工具本身没问题，可以跑一遍冒烟测试（只在临时目录读写，不碰 C 盘数据）：

```bat
python _tmp_menu_test.py
```

全部通过会输出 `SMOKE TEST OK`，它验证容量统计、硬链接去重、reparse 跳过、
增长归因的算术是否正确。文件名是创建时的临时命名，确认可用后可以直接删掉。

---

## 五、配置 `diskscope.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `roots` | `["C:\\"]` | 监控根目录，可多个 |
| `track_depth` | `5` | 跟踪深度。`C:\`=0，`C:\Users`=1，`C:\Users\x\AppData\Local`=4 |
| `big_file_mb` | `100` | 超过此大小的单文件单独建节点，便于定位"一个文件撑爆" |
| `keep_snapshots` | `400` | 保留快照数（每 6 小时一次 ≈ 100 天） |
| `exclude` | `[]` | 排除目录，支持 `*` 通配单层，如 `["D:\\VM\\*"]` |
| `dedupe_hardlinks` | `true` | 硬链接去重，精度优先 |
| `base_hours` | `24` | 报告默认与"多少小时前"的快照对比 |
| `detail_min_mb` | `1.0` | 明细表忽略小于此值的抖动 |
| `growth_alert_gb` | `5.0` | `watch` 模式告警阈值 |

> 想更细地看某一层，把 `track_depth` 调大（代价是 DB 变大、扫描略慢）。

---

## 六、已知边界

- **目录总量 < 磁盘已用是正常的**：卷影副本/系统还原（`System Volume Information`，
  即使管理员也常读不到）、被独占锁定的文件、NTFS 压缩/稀疏文件的大小语义差异。
  报告最后一块给了 `vssadmin list shadowstorage` 等命令直接核查。
- **`--fast` 模式下总量会偏大 6% 左右**（硬链接重复计数），趋势仍可用。
- 跨卷挂载点（如挂到 `C:\Data` 的另一个物理盘）会被跳过（reparse point），不计入 C: 总量。
- 计划任务以 SYSTEM 运行，`data/` 与 `reports/` 目录需要 SYSTEM 可写（放在本地盘即可）。

---

## 七、目录结构

```
diskscope/
  scanner.py    扫描引擎（迭代遍历 + 祖先聚合 + 链接/硬链接处理）
  store.py      SQLite 快照存储
  analyze.py    差值计算、增长归因、基线选择、热点库
  report.py     单文件 HTML 报告
  cli.py        命令行与交互菜单
  config.py     配置
  winutil.py    提权、格式化、长路径、CJK 对齐
run.py          启动入口（兼容 embeddable Python）
data/           SQLite 数据库 + 运行日志
reports/        报告输出（latest.html 为稳定入口）
```
