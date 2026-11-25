"""Perfila las columnas físicas de ambos Excel y ejecuta la conciliación SQL."""
from collections import Counter
from pathlib import Path
import csv
import sqlite3
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed"

def profile():
    with (OUT / "etapa2_perfil_columnas.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["archivo","hoja","columna","texto_observado_filas_1_a_15",
                         "filas_hoja","celdas_con_valor","celdas_vacias","formulas",
                         "formulas_sin_cache","tipos_cache"])
        count = 0
        for path in sorted((ROOT / "data/raw").glob("*.xlsx")):
            formulas = load_workbook(path, data_only=False)
            cached = load_workbook(path, data_only=True)
            for sheet in formulas:
                values = cached[sheet.title]
                for col, cells in enumerate(sheet.columns, 1):
                    observed = [values.cell(cell.row, col).value for cell in cells]
                    types = Counter(type(value).__name__ for value in observed if value is not None)
                    formula_flags = [cell.data_type == "f" for cell in cells]
                    headers = dict.fromkeys(str(value).strip() for value in observed[:15]
                                            if isinstance(value, str) and value.strip())
                    filled = sum(types.values())
                    writer.writerow([path.name,sheet.title,get_column_letter(col)," | ".join(headers),
                                     len(cells),filled,len(cells)-filled,sum(formula_flags),
                                     sum(flag and value is None for flag,value in zip(formula_flags,observed)),
                                     " | ".join(f"{key}={value}" for key,value in sorted(types.items()))])
                    count += 1
        print(f"Columnas físicas perfiladas: {count}")

def main():
    profile()
    with sqlite3.connect(OUT / "descargas.sqlite") as conn:
        conn.executescript((ROOT / "src/etapa2.sql").read_text())
        query = "SELECT * FROM etapa2_conciliacion ORDER BY planta, mes"
        result = conn.execute(query)
        with (OUT / "etapa2_conciliacion_sql.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([column[0] for column in result.description])
            writer.writerows(result)
        print(conn.execute("""SELECT COUNT(*), SUM(brecha_original_l), SUM(brecha_residual_l),
                                      SUM(factor_candidato != 1)
                               FROM etapa2_conciliacion""").fetchone())

if __name__ == "__main__":
    main()
