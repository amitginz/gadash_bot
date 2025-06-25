from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from datetime import date
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)


@app.route('/')
def index():
    try:
        df = pd.read_excel("works.xlsx")
        df = df.sort_values(by="תאריך", ascending=False)

        total_count = len(df)  # ⬅️ ספירה מלאה לפני סינון

        client = request.args.get("client", "").strip()
        date_filter = request.args.get("date", "").strip()

        if client:
            df = df[df["שם לקוח"].str.contains(client, case=False, na=False)]
        if date_filter:
            df = df[df["תאריך"] == date_filter]

        return render_template("index.html", records=df.to_dict(orient='records'), total_count=total_count)
    except Exception as e:
        return f"שגיאה בטעינת הקובץ: {e}"


def init_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("Gadash Data").sheet1
    return sheet


@app.route('/add', methods=["GET", "POST"])
def add():
    if request.method == "POST":
        df = pd.read_excel("works.xlsx")
        new_row = {col: request.form.get(col, "") for col in df.columns}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_excel("works.xlsx", index=False)

        # ניסיון לשמור גם ל-Google Sheets
        try:
            sheet = init_gsheet()
            sheet.append_row(list(new_row.values()))
        except Exception as e:
            print(f"שגיאה ב-Google Sheets: {e}")

        return redirect(url_for("index"))

    today = date.today().strftime("%Y-%m-%d")
    return render_template("add.html", today=today, success=True)


@app.route('/edit/<int:row_id>', methods=["GET", "POST"])
def edit(row_id):
    df = pd.read_excel("works.xlsx")

    # POST: עדכון ושמירה
    if request.method == "POST":
        try:
            for key in df.columns:
                df.at[row_id, key] = request.form.get(key)
            df.to_excel("works.xlsx", index=False)
            return redirect(url_for("index"))
        except Exception as e:
            return f"שגיאה בעדכון: {e}"

    # GET: שליחה לתבנית edit.html
    try:
        row_data = df.iloc[row_id].to_dict()
        return render_template("edit.html", row=row_data, row_id=row_id)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route('/delete/<int:row_id>')
def delete(row_id):
    df = pd.read_excel("works.xlsx")
    df = df.drop(index=row_id).reset_index(drop=True)
    df.to_excel("works.xlsx", index=False)
    return redirect(url_for("index"))


@app.route("/export")
def export():
    return send_file("works.xlsx", as_attachment=True, download_name="דוח_גדש.xlsx")


@app.route("/import", methods=["GET", "POST"])
def import_data():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            new_df = pd.read_excel(file)
            try:
                existing_df = pd.read_excel("works.xlsx")
                df = pd.concat([existing_df, new_df], ignore_index=True)
            except FileNotFoundError:
                df = new_df
            df.to_excel("works.xlsx", index=False)
            return redirect("/")
        else:
            return "יש לבחור קובץ Excel תקני (.xlsx)"
    return render_template("import.html")


if __name__ == '__main__':
    app.run(debug=True)
