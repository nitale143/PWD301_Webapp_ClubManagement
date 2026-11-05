"""Sinh docs/FUNCTION_REFERENCE.md từ docstring trong thư mục app/.

Chạy lại sau khi thêm/sửa hàm:  python scripts/gen_function_reference.py
Chỉ đọc mã nguồn bằng `ast` (không import app), nên không cần .env hay database.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "FUNCTION_REFERENCE.md"

GROUPS = [
    ("Khởi tạo & cấu hình", ["app/__init__.py", "app/config.py", "app/cli.py", "app/limiter.py", "app/timeutil.py"]),
    ("Bảo mật & xác thực", ["app/hardening.py", "app/security.py", "app/permissions.py", "app/tokens.py",
                             "app/validators.py", "app/forms.py", "app/mail.py"]),
    ("Dữ liệu", ["app/models.py", "app/legacy_sqlite.py", "app/scheduler.py"]),
    ("Routes (giao diện web & REST API)", ["app/routes/auth.py", "app/routes/admin.py", "app/routes/main.py",
                                           "app/routes/member.py", "app/routes/profile.py", "app/routes/event.py",
                                           "app/routes/funds.py", "app/routes/ai.py", "app/routes/api.py"]),
]


def _route_of(node):
    for decorator in node.decorator_list:
        text = ast.unparse(decorator)
        if any(part in text for part in (".route(", ".get(", ".post(", ".put(", ".patch(", ".delete(")):
            return text
    return ""


def _guards(node):
    names = []
    for decorator in node.decorator_list:
        text = ast.unparse(decorator)
        if any(word in text for word in ("required", "limiter")):
            names.append(text)
    return names


def _describe(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(ROOT).as_posix()
    lines = [f"### `{rel}`", ""]
    module_doc = ast.get_docstring(tree)
    if module_doc:
        lines += [module_doc.strip().split("\n\n")[0].replace("\n", " "), ""]
    rows = []

    def visit(body, prefix=""):
        for node in body:
            if isinstance(node, ast.ClassDef):
                rows.append((f"class `{prefix}{node.name}`", node.lineno, "", [], ast.get_docstring(node) or ""))
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = f"`{prefix}{node.name}({ast.unparse(node.args)})`"
                rows.append((signature, node.lineno, _route_of(node), _guards(node), ast.get_docstring(node) or ""))

    visit(tree.body)
    if not rows:
        return lines + ["_(không có hàm)_", ""]
    lines += ["| Hàm / class | Dòng | Mô tả |", "|---|---|---|"]
    for signature, lineno, route, guards, doc in rows:
        summary = " ".join(doc.strip().split()) if doc else "—"
        extra = []
        if route:
            extra.append(f"route: `{route}`")
        if guards:
            extra.append("bảo vệ: " + ", ".join(f"`{g}`" for g in guards))
        if extra:
            summary += "<br>" + " · ".join(extra)
        lines.append(f"| {signature.replace('|', '&#124;')} | {lineno} | {summary.replace('|', '&#124;')} |")
    lines.append("")
    return lines


def main():
    covered = {item for _title, items in GROUPS for item in items}
    services = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "app" / "services").glob("*.py")
                      if p.name != "__init__.py")
    groups = GROUPS + [("Services (nghiệp vụ & trợ lý AI)", services)]
    others = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "app").rglob("*.py")
                    if p.relative_to(ROOT).as_posix() not in covered | set(services)
                    and p.name != "__init__.py")
    if others:
        groups.append(("Khác", others))

    out = ["# Tham chiếu hàm (Function Reference)", "",
           "> File này được sinh tự động bởi `python scripts/gen_function_reference.py` từ docstring trong code.",
           "> Muốn sửa mô tả: sửa docstring của hàm rồi chạy lại script. Đọc kèm `docs/DEVELOPER_GUIDE.md`.", ""]
    for title, files in groups:
        anchor = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        out.append(f"- [{title}](#{anchor})")
    out.append("")
    for title, files in groups:
        out += [f"## {title}", ""]
        for rel in files:
            path = ROOT / rel
            if path.exists():
                out += _describe(path)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Đã ghi {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
