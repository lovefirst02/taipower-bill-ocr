"""What a Taipower bill prints, apart from names, addresses and figures.

The bill reader is trained to read only this, and its reading of a label is
taken only when it is one of these: a closed list of a few hundred characters
stays readable on a page too blurred for a model of all of Chinese.
"""

from __future__ import annotations

import re

LABELS = (
    # Block titles
    "電號 Customer Number", "繳費期限 Due Date", "應繳總金額 Total Amount", "繳費資訊 Payment Info.",
    "用戶資訊 Basic Info.", "計費內容 Charge Info.", "其他資訊 Other Info.", "發票資訊 Invoice",
    "前期發票資訊 Invoice", "本期載具號碼 Carrier", "收款章 Receipt Stamp", "扣款日期 Payment Date",
    "繳費日期 Payment Date", "電費計收說明 Charge Introduction", "節電資訊 Energy Saving Info.", "電表資料 Meter Info.",
    "電號", "繳費期限", "應繳總金額", "繳費資訊", "用戶資訊", "計費內容", "其他資訊", "發票資訊", "前期發票資訊",
    # kWh
    "計費度數(度)/Energy Consumption(kWh)", "計費度數(度)", "計費度數", "轉供度數(度)", "轉供度數", "前期轉供度數",
    "用電度數(度)", "本期轉供度數(度)", "尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數", "經常度數", "經常尖峰度數",
    "尖峰用電度數", "半尖峰用電度數", "週六半尖峰用電度數", "離峰用電度數", "經常用電度數", "優惠度數", "底度", "公共分攤戶數",
    # Charges
    "基本電費", "基本電費(約定)", "基本電費(非約定)", "流動電費", "功率因數調整費", "功率因數調整金額", "優惠金額", "電子帳單優惠減收金額",
    "電器租費", "分攤公共電費", "補收(退)金額(含停電扣減)", "基本折扣", "免印寄單據減收", "本期預繳", "稅前應繳總金額", "營業稅", "繳費總金額",
    "電費金額", "遲付費用", "其他費用",
    # Dates
    "計費期間", "用電計費期間", "用電期間", "本次抄表日", "下次抄表日", "抄表日", "本次收費日", "下次收費日", "本次", "下次", "收費日",
    "本次抄表日/收費日", "下次抄表日/收費日", "本次抄表日/扣款日", "下次抄表日/扣款日", "代收截止日", "本次收費日",
    # About the customer
    "電價種類", "時間種類", "用電種類", "用電地址", "行業別", "代繳帳號", "用戶營利事業統一編號", "本公司營利事業統一編號", "先生/女士/寶號",
    "通知單號碼", "單據號碼", "查核碼", "契約容量(瓩)", "最高需量(瓩)", "經常(尖峰)契約", "備用契約", "經常(尖峰)需量", "經常需量",
    "半尖峰(非夏月)需量", "週六半尖峰需量", "離峰需量", "備用率", "功率因數(%)", "調整係數(%)", "計費內容", "高壓電力", "低壓電力",
    "高壓需量電力非營業用電", "高壓電力綜合非營業用", "三段式時間電價", "二段式時間電價", "非時間電價", "表燈非營業用",
    # Other information
    "輪流停電組別", "饋線代號", "每度燃料成本", "本期碳排量", "電力排碳量", "每度繳交再生基金", "當期每度平均電價", "本期用電日數", "去年同期用電度", "去年同期用電日數",
    "發票期別", "發票號碼", "金額(元)",
)

_MARKS = {"（": "(", "）": ")", "：": ":", "％": "%", "／": "/", "～": "~", "，": ","}
_TRANSLATE = str.maketrans(_MARKS)


def normal(text: str) -> str:
    """Spaces out, half-width marks: how the reader writes what it reads."""
    return re.sub(r"\s+", "", text).translate(_TRANSLATE)


FIGURE_CHARS = "0123456789,.-/~*:%至元年月日"
_LETTERS = "".join(sorted({char for label in LABELS for char in normal(label) if char.isascii() and char.isalpha()}))
_CHINESE = "".join(sorted({char for label in LABELS for char in normal(label) if not char.isascii()} - set(FIGURE_CHARS)))
READER_CHARSET = FIGURE_CHARS + "()" + _LETTERS + _CHINESE

NORMAL_LABELS = frozenset(normal(label) for label in LABELS) | frozenset(normal(label) + ":" for label in LABELS)
_BY_LENGTH = sorted(NORMAL_LABELS, key=len, reverse=True)


def is_label(text: str) -> bool:
    return text in NORMAL_LABELS


def split_label(text: str) -> tuple[str, str] | None:
    """A label and the figures on the same line ("計費期間:113/01/01至113/01/31")."""
    for label in _BY_LENGTH:
        if text.startswith(label) and len(text) > len(label):
            rest = text[len(label):]
            if re.fullmatch(r"[\d,.\-/~*:%至元年月日]+", rest) and re.search(r"\d", rest):
                return label, rest
    return None
