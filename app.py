from flask import Flask, render_template, request, redirect, url_for
import pandas as pd

app = Flask(__name__)


@app.route('/')
def index():
    try:
        df = pd.read_excel("works.xlsx")
        df = df.sort_values(by="תאריך", ascending=False)
        return render_template("index.html", records=df.to_dict(orient='records'))
    except Exception as e:
        return f"שגיאה בטעינת הקובץ: {e}"

@app.route('/add', methods=["GET", "POST"])
def add():
    if request.method == "POST":
        df = pd.read_excel("works.xlsx")

        # מייצרים DataFrame חדש משורה בודדת
        new_row_df = pd.DataFrame([{
            col: request.form.get(col, "") for col in df.columns
        }])

        # מחברים את ה-DataFrame הקיים עם השורה החדשה
        df = pd.concat([df, new_row_df], ignore_index=True)

        df.to_excel("works.xlsx", index=False)
        return redirect(url_for("index"))

    return render_template("add.html")

@app.route('/edit/<int:row_id>', methods=["GET", "POST"])
def edit(row_id):
    df = pd.read_excel("works.xlsx")

    if request.method == "POST":
        for key in df.columns:
            df.at[row_id, key] = request.form.get(key)
        df.to_excel("works.xlsx", index=False)
        return redirect(url_for("index"))

    row_data = df.iloc[row_id].to_dict()
    return render_template("edit.html", row=row_data)


if __name__ == '__main__':
    app.run(debug=True)
