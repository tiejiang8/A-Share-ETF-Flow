# 数据字典

## etf_universe.parquet

- `etf_code`: ETF代码。
- `etf_name`: ETF简称。
- `expanded_name`: 扩位简称。
- `exchange`: SSE或SZSE。
- `manager`: 基金管理人。
- `tracking_index_code`: 跟踪指数代码。
- `tracking_index_name`: 跟踪指数名称。
- `is_a_share_equity_etf`: 是否进入A股权益ETF主分析池。
- `source/source_priority/source_url/retrieved_at/data_date/is_official/validation_status`: 数据血缘字段。

## etf_daily.parquet

- `open/high/low/close/prev_close/pct_change`: 行情字段。
- `volume/amount/amount_5d_mean/amount_vs_5d`: 成交字段。
- `latest_shares/previous_shares/shares_5d_ago`: 份额字段。
- `share_daily_change/share_5d_change`: 份额变化字段。
- `nav/fund_size_100m/premium_discount`: 净值、规模、折溢价。
- `estimated_net_subscription_100m`: 单日估算净申购/赎回，单位亿元。
- `flow_1d/flow_5d`: 单日和5日资金流，单位亿元。
- `flow_5d_valid_days/flow_5d_daily_avg`: 5日窗口有效交易日数及窗口日均资金流，仅作为保留字段。
- `flow_prev4_valid_days/flow_prev4_total/flow_prev4_daily_avg`: 当日前一阶段的有效交易日数、合计和日均资金流；标准完整窗口对应前4日。
- `flow_acceleration_vs_prev4/flow_acceleration_ratio_vs_prev4/flow_speed_status`: 当日相对前4日均值的资金加速度、可用时的相对加速度及速度状态；前4日均值接近0时比率为空。`flow_previous_4d/flow_acceleration/flow_acceleration_ratio` 为兼容别名。
- `temperature_level/temperature_direction/temperature_stage`: 统一计算的温度等级、方向和阶段；`temperature_phase` 为阶段兼容字段。
- `temperature`: 产品层资金温度，0至100。

## cluster_daily.parquet

- `member_count`: 集群成员数。
- `total_size_100m`: 集群总规模，单位亿元。
- `estimated_net_subscription_100m`: 集群单日估算净申购/赎回，单位亿元。
- `estimated_net_subscription_5d_100m`: 集群5日估算净申购/赎回，单位亿元。
- `flow_1d/flow_5d/flow_prev4_total/flow_prev4_daily_avg/flow_acceleration_vs_prev4/flow_acceleration_ratio_vs_prev4/flow_speed_status`: 集群资金速度与方向反转字段。
- `gross_inflow/gross_outflow/net_flow/gross_turnover_flow/flow_offset_ratio/flow_offset_status`: 集群成员资金毛流入、毛流出、净额、毛流量、抵消率及内部状态。
- `max_inflow_etf_*/max_outflow_etf_*`: 集群最大流入和最大流出ETF代码、名称及金额。
- `flow_structure_label`: 结合净额/规模、成员广度、同向贡献集中度、成员数和双向毛流量生成的资金结构标签。
- `weighted_pct_change`: 规模加权涨跌幅。
- `cluster_temperature`: 集群层资金温度，0至100。

## weekly_etf.parquet / weekly_cluster.parquet

- 周度ETF和集群结果复用上述 `flow_*` 速度字段，其中 `flow_1d` 为周末最后一个有效交易日资金，`flow_5d` 为本周有效资金合计，`flow_prev4_daily_avg` 表示周末之前阶段的日均资金流。
- `week_start_flow_100m/week_end_flow_100m/weekly_flow_direction_change`: 周初、周末资金金额及方向变化。
- 周度集群结果同时保留 `gross_*`、`flow_offset_*`、最大流入/流出ETF和 `flow_structure_label`。
- `weekly_snapshot.json` 保存完整周度ETF与集群记录；日报目录下的 `daily_snapshot.json` 保存完整日度ETF与集群记录。
