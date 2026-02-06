import io
from itertools import tee
from zipfile import ZipFile

import ezdxf
from flask import Flask, render_template, request, send_file
import pandas as pd

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def pairwise(iterable):
    # pairwise('ABCDEFG') --> AB BC CD DE EF FG
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html", error="")


@app.route("/convert", methods=["GET", "POST"])
def convert():
    if request.method == "POST":
        try:
            f = request.files.get("file")
            filename, extension = f.filename.replace(" ", "_").rsplit(".", 1)
            if not extension.lower() == "dxf":
                raise ezdxf.DXFTypeError
            buffer = io.BytesIO(f.read())
            wrapper = io.TextIOWrapper(buffer, encoding="utf-8")
            doc = ezdxf.read(wrapper)
        except (ezdxf.DXFTypeError, UnicodeDecodeError, ValueError):
            return render_template(
                "index.html", error="Wybierz plik w formacie DXF minimum 2007."
            )
        except ezdxf.DXFStructureError:
            return render_template("index.html", error="Niepoprawny lub zepsuty plik.")
        except Exception as e:
            return render_template("index.html", error=str(e))
        msp = doc.modelspace()
        if doc.header["$INSUNITS"] == 4:  # milimetry
            u = 1e-3
        elif doc.header["$INSUNITS"] == 5:  # centymetry
            u = 1e-2
        else:  # pozostałe
            u = 1
        for polyline in msp.query("LWPOLYLINE"):
            polyline.explode()
        entities = []
        for i, e in enumerate(msp.query(), start=1):
            if e.dxf.dxftype in ["3DFACE"]:
                entities.append(
                    pd.DataFrame(
                        [loc * u for loc in e.wcs_vertices()], columns=["X", "Y", "Z"]
                    ).assign(
                        i=i,
                        Prz=float("nan"),
                        g=(
                            float(request.form["g"])
                            if e.dxf.color == 256
                            else e.dxf.color
                        ),
                    )
                )
            elif e.dxf.dxftype in ["LINE"]:
                entities.append(
                    pd.DataFrame(
                        [
                            [
                                *(e.dxf.start * u),
                                i,
                                0 if e.dxf.color == 256 else e.dxf.color,
                            ]
                        ],
                        columns=["X", "Y", "Z", "i", "Prz"],
                    )
                )
                entities.append(
                    pd.DataFrame(
                        [
                            [
                                *(e.dxf.end * u),
                                i,
                                0 if e.dxf.color == 256 else e.dxf.color,
                            ]
                        ],
                        columns=["X", "Y", "Z", "i", "Prz"],
                    )
                )
            elif e.dxf.dxftype in ["ARC", "ELLIPSE", "CIRCLE"]:
                for segment in e.flattening(6):
                    entities.append(
                        pd.DataFrame(
                            [
                                [
                                    *(segment * u),
                                    i,
                                    0 if e.dxf.color == 256 else e.dxf.color,
                                ]
                            ],
                            columns=["X", "Y", "Z", "i", "Prz"],
                        )
                    )
        df = pd.concat(entities, ignore_index=True)
        df.index += 1
        if len(df.index) == 0:
            return render_template("index.html", error="Nieprawidłowa geometria.")
        points = [
            pd.DataFrame([point.dxf.location * u], columns=["X", "Y", "Z"])
            for point in msp.query("POINT")
        ]
        wezly = pd.concat([df[["X", "Y", "Z"]], *points], ignore_index=True).round(3)
        wezly.name = f"Wezly-{filename}.txt"
        prety = []
        for _, group in df[df["Prz"].notna()].groupby("i"):
            for x, y in pairwise(group.index):
                prety.append([x, y, 0, 0, 0, int(group["Prz"].min())])
        prety = pd.DataFrame(prety, columns=["wI", "wJ", "wK", "Kier", "Mat", "Prz"])
        prety.name = f"Prety-{filename}.txt"
        plaskie = []
        for _, group in df[df["Prz"].isna()].groupby("i"):
            plaskie.append([*group.index, *[0] * (5 - len(group)), group.g.min() / 100])
        plaskie = pd.DataFrame(plaskie, columns=["w1", "w2", "w3", "w4", "w5", "g[m]"])
        plaskie.name = f"Plaskie-{filename}.txt"
        files = {}
        for df in [wezly, prety, plaskie]:
            df.index += 1
            if len(df.index) > 0:
                with io.StringIO() as buffer:
                    df.to_csv(
                        buffer,
                        sep=" ",
                        decimal=",",
                        lineterminator="\r\n",
                        header=False,
                    )
                    mem = io.BytesIO()
                    mem.write(bytes(df.name.split("-")[0] + "\r\n", "utf-8"))
                    mem.write(buffer.getvalue().encode())
                    mem.seek(0)
                    files[df.name] = mem
        output = io.BytesIO()
        if request.form["b"].startswith("Dwa"):
            download_name = f"{filename}.zip"
            with ZipFile(output, "w") as zip_file:
                for name, mem in files.items():
                    zip_file.writestr(name, mem.getvalue())
        else:
            download_name = f"{filename}.txt"
            for mem in files.values():
                output.write(mem.getvalue())
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=download_name,
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
