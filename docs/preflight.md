# 单证预检：输入与边界

预检比较已抄录字段与当前确认业务事实，输出可复核的问题清单。它不解析或鉴定附件，也不替人批准出运。

## 输入

执行 `python scripts/preflight_demo.py` 会生成可直接复用的完整 `before-input.json` 和 `after-input.json`。字段如下：

| 字段 | 含义 |
| --- | --- |
| `shipment` | `shipping.models.Shipment` 的 JSON 数据；金额用十进制字符串；`facts` 中保留基准值与人工确认 |
| `scope` | 一个明确的合同 ID；不接受空范围或 `ALL` |
| `documents` | 当前合同的三个声明来源：`invoice`、`packing`、`carrier`；各有 `id`、`file_name`、`doc_role`、`scope` |
| `observations` | 抄录记录；每条含 `check_id`、`document_id`、`doc_role`、`scope`、`field_path`、`value`、`unit`、`source_file`、`source_locator`、`reviewed` |
| `synthetic_only` | 合成示例为 `true`；真实资料省略或设为 `false` |

`check_id` 为 `{document_id}:{field_path}`。例如：

```json
{
  "check_id": "packing:lines.0.packing_spec.carton_gross_g",
  "document_id": "packing",
  "doc_role": "packing_list",
  "scope": "TEST-CONTRACT-A",
  "field_path": "lines.0.packing_spec.carton_gross_g",
  "value": "5.000",
  "unit": "kg",
  "source_file": "SYNTHETIC-packing-list.txt",
  "source_locator": "合成文本第 6 行",
  "reviewed": true
}
```

清单由业务主数据生成：发票号、收货人、客户币种/总金额、逐货物基础数量；装箱单逐货物批号、基础数量、满箱箱数、单箱毛重；货代回件目的地。每增加一行货物就增加相应必填检查，缺少对应来源记录仍会保留该项。

## 规则

1. 缺字段、未确认基准、空白来源定位、文件/角色/范围错配均不通过。
2. 同一清单字段有多条记录时要求明确保留哪一条；不会选择其中一个相符值就放行。未知清单记录同样保留并阻断。
3. 十进制精确比较，不接受来源记录自行设置精度来掩盖差异。不接受千分位、指数、负数、NaN 等格式；金额应使用无分组的十进制字符串。仅内置同单位与 kg → g；其他转换必须先明确适配。
4. 相符但未核验来源时是 `NEEDS_REVIEW`。派生金额需要相关数量、价格、合同币种和调整项仍与已确认事实相符。
5. 当前合同的基础数量/包装与价格计算仍执行现有校验；单证抄录相符不能覆盖错误业务计算。
6. JSON 报告带 SHA-256 输入指纹。`is_report_current()` 比较规则版本、完整主数据、范围、来源记录和声明文件清单；任何一项变化都使旧报告失效。

文件清单的存在只验证引用关系，不证明文件本身存在或内容未被改动。将预检接入真实附件时，应在调用前使用现有工作台的附件校验结果，并保留有效的来源记录。

## 浏览器和 Python 的分工

浏览器以固定的 Python 基准快照为输入，允许修改合成来源值、定位和核验状态。它不重新计算业务金额或修改合同范围。其报告的指纹范围为 `browser-checks`，与 Python 完整输入指纹不能互换。

`python scripts/preflight_demo.py --write-browser-data` 会重建静态快照及合成文本。CI 对比 Python/JavaScript 的状态结果，并检查生成文件没有漂移。

## 当前没有覆盖

所有国家/客户的单证要求、完整运输及海关规则、真实模板排版、附件内容自动识别、原件真实性鉴定、审签与正式出运放行。`READY_FOR_HUMAN_REVIEW` 仅表示当前这组检查没有未解决项目。
