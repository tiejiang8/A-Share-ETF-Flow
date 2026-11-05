# W28 历史净值补采修复

## 修复内容

1. 新增 `backfill-nav` 命令，按日期从中国证监会基金电子披露网站批量抓取历史单位净值，并按 `date + etf_code` 写入本地缓存。
2. `rebuild` 预取范围现在包含行情、份额和净值；补到新数据后会重新计算旧日报快照，不再只重绘旧报告。
3. 日报计算增加日期防护：报告日不得使用未来日期净值。
4. 新增 W28 一键脚本 `scripts/backfill_w28.ps1`。
5. 新增历史净值分页和未来净值隔离回归测试。

## W28 执行

```powershell
.\scripts\backfill_w28.ps1
```

等价于：

```powershell
python -m etf_temperature.cli backfill-nav --start 20260706 --end 20260709
python -m etf_temperature.cli rebuild --start 20260706 --end 20260710
python -m etf_temperature.cli run-weekly --week 2026-W28
```

补采覆盖率结果：

```text
outputs/nav_backfill_2026-07-06_2026-07-09.json
```
