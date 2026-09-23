from decimal import Decimal
from typing import Any, Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Count = Annotated[int, Field(ge=0, strict=True)]
Positive = Annotated[int, Field(gt=0, strict=True)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)


class SourceRef(Model):
    file_id: str | None = None
    locator: str | None = None
    raw_text: str | None = None
    manual_note: str | None = None


class Fact(Model):
    value: Any = None
    source_ref: SourceRef
    confirmed: bool = False


class Party(Model):
    name_cn: str | None = None
    name_en: str | None = None
    address: str | None = None
    contact: str | None = None
    phone: str | None = None
    email: str | None = None
    registration_no: str | None = None


class Price(Model):
    unit_price: Decimal | None = None
    currency: str | None = None
    pricing_unit: str | None = None
    base_units_per_pricing_unit: Positive | None = None
    integer_units: bool = True
    pricing_basis: Literal['auto','base','inner','carton','custom'] = 'auto'
    conversion_note: str | None = None

    @field_validator('unit_price', mode='before')
    @classmethod
    def decimal_string(cls, v):
        if isinstance(v, float):
            raise ValueError('金额必须使用十进制字符串')
        if v is not None and (not Decimal(v).is_finite() or Decimal(v) < 0):
            raise ValueError('价格必须为非负有限值')
        return v


class Adjustment(Model):
    label: str
    amount: Decimal
    evidence: SourceRef

    @field_validator('amount', mode='before')
    @classmethod
    def finite_amount(cls,value):
        if isinstance(value,float) or not Decimal(value).is_finite():raise ValueError('调整金额须为有限十进制字符串')
        return value


class Contract(Model):
    id: str
    contract_no: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    ordered_quantities: dict[str, Count] = Field(default_factory=dict)
    customer_currency: str | None = None
    customs_currency: str | None = None
    customer_adjustments: list[Adjustment] = Field(default_factory=list)
    customs_adjustments: list[Adjustment] = Field(default_factory=list)
    common_fields: dict[str, str | None] = Field(default_factory=dict)

    @field_validator('common_fields')
    @classmethod
    def allowed_common(cls, value):
        allowed = {'moc_no','insurance_no','marks','bank_name','bank_account','bank_swift','bank_address',
                   'purchase_contract_no','insurance_scope','salesperson','customs_registration','dangerous_class',
                   'origin_country','freight_note','warehouse_date','own_marks','split_transshipment'}
        if value.keys() - allowed:
            raise ValueError('COMMON_FIELDS_NOT_ALLOWED: ' + ','.join(value.keys() - allowed))
        return value


class PackingSpec(Model):
    outer_l_mm: Positive | None = None
    outer_w_mm: Positive | None = None
    outer_h_mm: Positive | None = None
    carton_net_g: Count | None = None
    carton_gross_g: Positive | None = None
    allow_rotate_90: bool = True
    max_layers: Positive | None = None
    max_goods_height_mm: Positive | None = None
    max_superimposed_g: Count | None = None
    temp_min_c: Decimal | None = None
    temp_max_c: Decimal | None = None
    availability: dict[str, Literal['known','unknown','not_applicable']] = Field(default_factory=dict)

    @model_validator(mode='after')
    def weights(self):
        if self.carton_gross_g is not None and self.carton_net_g is not None and self.carton_gross_g < self.carton_net_g:
            raise ValueError('GROSS_BELOW_NET')
        if self.temp_min_c is not None and self.temp_max_c is not None and self.temp_min_c > self.temp_max_c:
            raise ValueError('TEMPERATURE_RANGE_INVALID')
        return self


class PartialCarton(Model):
    id: str
    base_quantity: Positive
    outer_l_mm: Positive
    outer_w_mm: Positive
    outer_h_mm: Positive
    net_g: Count
    gross_g: Positive
    stack_rule: dict[str, Count | None] = Field(default_factory=dict)

    @model_validator(mode='after')
    def weights(self):
        if self.gross_g < self.net_g:
            raise ValueError('GROSS_BELOW_NET')
        return self


class ReferenceSample(Model):
    name: str
    batch_no: str | None = None
    quantity: Positive | None = None
    unit: str | None = None
    free_confirmed: bool = False
    included_in_carton_gross: bool = False
    packed_carton_id: str | None = None
    separate_line_id: str | None = None


class ShipmentLine(Model):
    id: str
    contract_id: str
    product_code: str | None = None
    name_cn: str | None = None
    name_en: str | None = None
    strength_text: str | None = None
    batch_no: str | None = None
    mfg_date: str | None = None
    exp_date: str | None = None
    base_unit: str | None = None
    inner_unit: str | None = None
    allowed_pallet_ids: list[str] = Field(default_factory=list)
    packing_extras: dict[str, Count | None] | None = None
    base_quantity: Count | None = None
    units_per_inner: Positive | None = None
    inners_per_carton: Positive | None = None
    full_cartons: Count | None = None
    partial_cartons: list[PartialCarton] = Field(default_factory=list)
    packing_spec: PackingSpec = Field(default_factory=PackingSpec)
    customer_price: Price = Field(default_factory=Price)
    customs_price: Price = Field(default_factory=Price)
    hs_code: str | None = None
    storage_text: str | None = None
    reference_samples: list[ReferenceSample] = Field(default_factory=list)


class PalletSpec(Model):
    id: str
    name: str
    length_mm: Positive | None = None
    width_mm: Positive | None = None
    height_mm: Count | None = None
    tare_g: Count | None = None
    max_payload_g: Positive | None = None
    availability: dict[str, Literal['known','unknown','not_applicable']] = Field(default_factory=dict)


class PackingExtras(Model):
    extra_height_mm: Count | None = None
    extra_length_mm: Count | None = None
    extra_width_mm: Count | None = None
    auxiliary_g: Count | None = None


class Vehicle(Model):
    name: str
    thermal_type: Literal['ambient','cold_chain'] = 'ambient'
    usable_l_mm: Positive | None = None
    usable_w_mm: Positive | None = None
    usable_h_mm: Positive | None = None
    door_w_mm: Positive | None = None
    door_h_mm: Positive | None = None
    payload_g: Positive | None = None
    temp_min_c: Decimal | None = None
    temp_max_c: Decimal | None = None


class RouteLeg(Model):
    id: str
    mode: str
    max_unit_height_mm: Positive | None = None
    max_unit_gross_g: Positive | None = None
    clearance_mm: Count = 0
    vehicle_snapshot: Vehicle | None = None
    constraints_confirmed: bool = False
    height_includes_pallet: bool | None = None
    repalletize: bool = False
    availability: dict[str, Literal['known','unknown','not_applicable']] = Field(default_factory=dict)


class Shipment(Model):
    id: str
    business_no: str
    revision: Count = 0
    schema_version: int = 1
    shipper: Party = Field(default_factory=Party)
    consignee: Party = Field(default_factory=Party)
    notify_party: Party = Field(default_factory=Party)
    manufacturer: Party = Field(default_factory=Party)
    origin: str | None = None
    destination: str | None = None
    transport_mode: str | None = None
    trade_term: str | None = None
    payment_term: str | None = None
    planned_ship_date: str | None = None
    remarks: str | None = None
    contracts: list[Contract] = Field(default_factory=list)
    lines: list[ShipmentLine] = Field(default_factory=list)
    route_legs: list[RouteLeg] = Field(default_factory=list)
    pallet_choices: list[PalletSpec] = Field(default_factory=list)
    extras: PackingExtras = Field(default_factory=PackingExtras)
    transport_details: dict[str, str | None] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    audit: list[dict] = Field(default_factory=list)
    currency_decimals: dict[str, Count] = Field(default_factory=lambda: {'EUR':2,'USD':2,'CNY':2})

    @field_validator('id')
    @classmethod
    def safe_id(cls,value):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise ValueError('SHIPMENT_ID_INVALID')
        return value

    @model_validator(mode='after')
    def links(self):
        contracts = [c.id for c in self.contracts]
        lines = [l.id for l in self.lines]
        if len(set(contracts)) != len(contracts) or len(set(lines)) != len(lines):
            raise ValueError('DUPLICATE_ID')
        if any(l.contract_id not in contracts for l in self.lines):
            raise ValueError('UNKNOWN_CONTRACT')
        for line in self.lines:
            if set(line.allowed_pallet_ids)-{p.id for p in self.pallet_choices}:raise ValueError('PALLET_BINDING_INVALID')
            if line.packing_extras is not None:PackingExtras.model_validate(line.packing_extras)
        return self


class Observation(Model):
    file_id: str | None = None
    doc_role: str
    scope: str | None = None
    field_path: str
    value: Any = None
    unit: str | None = None
    source_ref: SourceRef
    extraction_status: str = 'CANDIDATE'
    decimals: Count | None = None


class Issue(Model):
    code: str
    severity: Literal['error','warning','info'] = 'warning'
    status: Literal['PASS','FAIL','NOT_CHECKED','NEEDS_CONFIRMATION'] = 'NOT_CHECKED'
    scope: str | None = None
    field_path: str | None = None
    expected: Any = None
    observed: Any = None
    source_refs: list[dict] = Field(default_factory=list)
    message: str = ''
    resolution: str | None = None
    resolved_at: str | None = None


class Pallet(Model):
    id:str
    line_id:str
    contract_id:str
    carton_count:Positive
    carton_ids:list[str]
    pallet_spec_snapshot:PalletSpec
    layout:list[dict[str,int]]
    top_layout:list[dict[str,int]]
    per_layer:Positive
    layers:Positive
    boxes_last_layer:Positive
    estimated:dict[str,Count|None]
    actual:dict|None=None
    extras:PackingExtras
    approval:dict|None=None
    capacity:Positive
    box_height_mm:Positive
    group:str


class Plan(Model):
    id:str|None=None
    input_hash:str
    algorithm_version:str
    pallets:list[Pallet]
    totals:dict
    route_checks:list[Issue]
    issues:list[Issue]
    status:Literal['provisional','confirmed','stale']='provisional'
    manual_counts:dict[str,list[Positive]]=Field(default_factory=dict)
    layout_index:Count=0
    source_revision:Count|None=None
    created_at:str|None=None
    confirmed_at:str|None=None
    supersedes:str|None=None


class DocumentRecord(Model):
    id:str
    type:str
    scope:str
    template_id:str
    template_version:str
    template_hash:str
    input_hash:str
    source_revision:Count
    stage:Literal['draft','approved','released','stale']='draft'
    previous_stage:str|None=None
    file_path:str
    file_hash:str
    issues:list[str]=Field(default_factory=list)
    approved_at:str|None=None
    created_at:str


def convert_unit(value, unit, kind):
    scales = {'dimension': {'mm':1, 'cm':10, 'm':1000}, 'weight': {'g':1, 'kg':1000}}
    converted = Decimal(str(value)) * scales[kind][unit]
    if not converted.is_finite() or converted < 0 or converted != converted.to_integral_value():
        raise ValueError('UNIT_PRECISION_INVALID')
    return int(converted)


def leaves(data, prefix=''):
    if isinstance(data, dict):
        for k, v in data.items():
            yield from leaves(v, f'{prefix}.{k}' if prefix else k)
    elif isinstance(data, list):
        for i,v in enumerate(data):
            yield from leaves(v, f'{prefix}.{i}')
    else:
        yield prefix, data
