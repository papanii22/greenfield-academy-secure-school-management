from flask import Blueprint, render_template

from Routes.auth import role_required


admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/admin")
@role_required("admin")
def index():
    return render_template("dashboard.html", role_label="Administrator")
