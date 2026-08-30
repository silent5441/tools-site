import io

from pandas import read_excel, read_csv, DataFrame, ExcelWriter


def _write_csv(df: DataFrame) -> bytes:
    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue().encode("utf-8-sig")


def _read_csv(data: bytes) -> DataFrame:
    try:
        return read_csv(io.BytesIO(data))
    except Exception:
        return read_csv(io.BytesIO(data), encoding="latin-1")


def xlsx_to_csv(data: bytes) -> bytes:
    df = read_excel(io.BytesIO(data))
    return _write_csv(df)


def csv_to_xlsx(data: bytes) -> bytes:
    df = _read_csv(data)
    out = io.BytesIO()
    with ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return out.getvalue()
