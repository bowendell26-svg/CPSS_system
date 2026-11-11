# =====================================================================
# CHURCH PROGRAM SCHEDULING SYSTEM
# app.py  -  BAHAGI 4: Setup, Login, Dashboard, Members, at Programs
# =====================================================================

import os
import calendar as calendar_module
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask, render_template, redirect, url_for, request, flash, session
)
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from sqlalchemy import inspect
from werkzeug.security import generate_password_hash, check_password_hash


# =====================================================================
# 1. SETUP AT SUPABASE DATABASE CONFIGURATION
# =====================================================================

app = Flask(__name__)

# Sikretong susi para sa login session. Palitan ito bago i-deploy.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "palitan-mo-ito-bago-i-deploy")
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", app.config["MAIL_USERNAME"])
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "false").lower() in ("1", "true", "yes")

# Supabase PostgreSQL Database URI configuration na may SQLite fallback
database_url = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///church.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# Awtomatikong ma-lo-logout ang admin pagkalipas ng 8 oras
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# Ang db ang ginagamit natin para makipag-usap sa database
db = SQLAlchemy(app)

# Ang LoginManager ang nagbabantay kung sino ang naka-login
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"


# =====================================================================
# 2. MGA TABLE (MODELS)
# =====================================================================

class User(UserMixin, db.Model):
    """Account ng admin (ang may login)."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)    # hindi kailanman plain na password
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254))
    role = db.Column(db.String(20), default="admin")             # admin
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)


class Member(db.Model):
    """Mga miyembro ng church na pwedeng bigyan ng bahagi."""
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(50))               # maikling pangalan
    contact_number = db.Column(db.String(30))
    is_active = db.Column(db.Boolean, default=True)

    @property
    def label(self):
        """Ang pangalang ipapakita sa mga card at sa programa:
        ang display name kung meron, kung wala ay ang buong pangalan."""
        return self.display_name or self.full_name


class ProgramType(db.Model):
    """Ang uri ng programa: Sabbath School, Wednesday, Friday."""
    __tablename__ = "program_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)

    template_parts = db.relationship(
        "ProgramTypePart",
        backref="program_type",
        order_by="ProgramTypePart.sort_order",
    )


class PartType(db.Model):
    """Katalogo ng mga bahagi (Opening song, Gaceta, atbp.)."""
    __tablename__ = "part_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    category = db.Column(db.String(20))                   # song, prayer, talk, report, study
    needs_detail = db.Column(db.Boolean, default=False)   # kailangan ba ng pamagat/paksa


class Song(db.Model):
    """Kanta at lyrics na puwedeng iugnay sa mga bahagi ng programa."""
    __tablename__ = "songs"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    lyrics = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProgramTypePart(db.Model):
    """Template: aling bahagi ang kasama sa aling programa, at sa anong pagkakasunod."""
    __tablename__ = "program_type_parts"

    id = db.Column(db.Integer, primary_key=True)
    program_type_id = db.Column(db.Integer, db.ForeignKey("program_types.id"), nullable=False)
    part_type_id = db.Column(db.Integer, db.ForeignKey("part_types.id"), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)
    is_required = db.Column(db.Boolean, default=True)

    part_type = db.relationship("PartType")


class MonthlyElderAssignment(db.Model):
    """Pangalan ng elder na naka-assign para sa isang partikular na buwan."""
    __tablename__ = "monthly_elder_assignments"
    __table_args__ = (
        db.UniqueConstraint("year", "month", name="uq_monthly_elder_year_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    elder_name = db.Column(db.String(100), nullable=False)


class Program(db.Model):
    """Isang aktwal na programa sa isang petsa."""
    __tablename__ = "programs"
    __table_args__ = (
        db.UniqueConstraint("program_type_id", "program_date", name="uq_program_type_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    program_type_id = db.Column(db.Integer, db.ForeignKey("program_types.id"), nullable=False)
    program_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="draft")    # draft, published, done, cancelled
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    program_type = db.relationship("ProgramType")
    items = db.relationship(
        "ProgramItem",
        backref="program",
        cascade="all, delete-orphan",
        order_by="ProgramItem.sort_order",
    )


class ProgramItem(db.Model):
    """Ang bawat bahagi ng isang programa at kung sino ang naka-assign."""
    __tablename__ = "program_items"

    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey("programs.id"), nullable=False)
    part_type_id = db.Column(db.Integer, db.ForeignKey("part_types.id"), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)

    member_id = db.Column(db.Integer, db.ForeignKey("members.id"))   # miyembro
    guest_name = db.Column(db.String(100))                           # bisita (tina-type)

    detail = db.Column(db.String(200))                    # pamagat ng kanta, paksa, atbp.
    song_id = db.Column(db.Integer, db.ForeignKey("songs.id"))
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_at = db.Column(db.DateTime)

    part_type = db.relationship("PartType")
    member = db.relationship("Member")
    song = db.relationship("Song")
    assigner = db.relationship("User")


# =====================================================================
# 3. MGA BAHAGI AT TEMPLATE NG 3 PROGRAMA (awtomatikong ilalagay)
# =====================================================================

PART_INFO = {
    "Singspiration":     ("song",   True),
    "Opening song":      ("song",   True),
    "Opening prayer":    ("prayer", False),
    "Sec. Report":       ("report", False),
    "Special song":      ("song",   True),
    "Promotional talk":  ("talk",   True),
    "Intermission song": ("song",   True),
    "Gaceta":            ("report", False),
    "Lesson study":      ("study",  True),
    "Message":           ("talk",   True),
    "Special prayer":    ("prayer", False),
    "Closing song":      ("song",   True),
    "Closing prayer":    ("prayer", False),
}

SABBATH_SCHOOL = [
    "Singspiration", "Opening song", "Opening prayer", "Sec. Report",
    "Special song", "Promotional talk", "Intermission song", "Gaceta",
    "Lesson study", "Closing song", "Closing prayer",
]

WEDNESDAY_PROGRAM = [
    "Singspiration", "Opening song", "Opening prayer", "Special song",
    "Message", "Special prayer", "Closing song", "Closing prayer",
]

FRIDAY_PROGRAM = [
    "Singspiration", "Opening song", "Opening prayer", "Special song",
    "Message", "Closing song", "Closing prayer",
]


def seed_defaults():
    parts = {}
    for name, (category, needs_detail) in PART_INFO.items():
        part = PartType.query.filter_by(name=name).first()
        if not part:
            part = PartType(name=name, category=category, needs_detail=needs_detail)
            db.session.add(part)
        parts[name] = part

    templates = [
        ("Sabbath School",    5, SABBATH_SCHOOL),     # Sabado
        ("Divine Service",    5, []),                 # Sabado
        ("AY Program",        5, []),                 # Sabado
        ("Sunday Program",    6, []),                 # Linggo
        ("Monday Program",    0, []),                 # Lunes
        ("Tuesday Program",   1, []),                 # Martes
        ("Wednesday Program", 2, WEDNESDAY_PROGRAM),  # Miyerkules
        ("Thursday Program",  3, []),                 # Huwebes
        ("Friday Program",    4, FRIDAY_PROGRAM),     # Biyernes
    ]
    for name, weekday, part_names in templates:
        program_type = ProgramType.query.filter_by(name=name).first()
        is_new_type = program_type is None
        if not program_type:
            program_type = ProgramType(name=name, weekday=weekday)
            db.session.add(program_type)
            db.session.flush()

        if not is_new_type:
            continue

        for order, part_name in enumerate(part_names, start=1):
            db.session.add(ProgramTypePart(
                program_type=program_type,
                part_type=parts[part_name],
                sort_order=order,
            ))

    db.session.commit()


# =====================================================================
# 4. TULONG NA MGA FUNCTION
# =====================================================================

def create_program_from_template(program_type_id, program_date):
    existing = Program.query.filter_by(
        program_type_id=program_type_id, program_date=program_date
    ).first()
    if existing:
        return None

    program = Program(program_type_id=program_type_id, program_date=program_date)
    db.session.add(program)

    program_type = db.session.get(ProgramType, program_type_id)
    for template_part in program_type.template_parts:
        program.items.append(ProgramItem(
            part_type_id=template_part.part_type_id,
            sort_order=template_part.sort_order,
        ))

    db.session.commit()
    return program


def admin_exists():
    return User.query.filter_by(role="admin").first() is not None


def count_unassigned(program):
    return sum(
        1 for item in program.items
        if item.member_id is None and not item.guest_name
    )


def next_date_for_weekday(weekday):
    today = date.today()
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def is_safe_next(url):
    return bool(url) and url.startswith("/") and not url.startswith("//")


def is_valid_email(email):
    return bool(email and len(email) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


def password_reset_serializer(user):
    salt = f"password-reset:{user.password_hash}:{user.email}"
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=salt)


def send_password_reset_email(user, token):
    mail_server = app.config["MAIL_SERVER"]
    sender = app.config["MAIL_DEFAULT_SENDER"]
    public_base_url = app.config["PUBLIC_BASE_URL"]
    if not mail_server or not sender or not public_base_url:
        raise RuntimeError(
            "PUBLIC_BASE_URL, MAIL_SERVER, and MAIL_DEFAULT_SENDER must be configured."
        )
    if app.config["MAIL_USE_TLS"] and app.config["MAIL_USE_SSL"]:
        raise RuntimeError("Configure either MAIL_USE_TLS or MAIL_USE_SSL, not both.")
    if app.config["MAIL_USERNAME"] and not (
        app.config["MAIL_USE_TLS"] or app.config["MAIL_USE_SSL"]
    ):
        raise RuntimeError("Enable TLS or SSL when SMTP authentication is configured.")

    reset_path = url_for("reset_password", user_id=user.id, token=token)
    reset_url = f"{public_base_url}{reset_path}"
    message = EmailMessage()
    message["Subject"] = "Reset your Church Program password"
    message["From"] = sender
    message["To"] = user.email
    message.set_content(
        "We received a request to reset your Church Program password.\n\n"
        f"Use this link within one hour:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    smtp_class = smtplib.SMTP_SSL if app.config["MAIL_USE_SSL"] else smtplib.SMTP
    with smtp_class(
        mail_server,
        app.config["MAIL_PORT"],
        timeout=10,
    ) as smtp:
        if app.config["MAIL_USE_TLS"]:
            smtp.starttls()
        if app.config["MAIL_USERNAME"]:
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# =====================================================================
# 5. ADMIN SETUP, LOGIN, AT LOGOUT
# =====================================================================

@app.route("/")
def index():
    if not admin_exists():
        return redirect(url_for("setup"))
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if admin_exists():
        return redirect(url_for("login"))

    form = {"full_name": "", "username": "", "email": ""}

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        form = {"full_name": full_name, "username": username, "email": email}

        error = None
        if not full_name or not username or not email or not password:
            error = "Please fill in all fields."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif not is_valid_email(email):
            error = "Please enter a valid email address."
        elif User.query.filter_by(email=email).first():
            error = "That email address is already in use."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if error:
            flash(error, "error")
        else:
            admin = User(
                username=username,
                full_name=full_name,
                email=email,
                password_hash=generate_password_hash(password),
                role="admin",
            )
            db.session.add(admin)
            db.session.commit()
            flash("Admin account created. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("setup.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        return redirect(url_for("setup"))

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    next_url = request.values.get("next", "")

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            login_user(user)
            session.permanent = True
            user.last_login = datetime.utcnow()
            db.session.commit()

            if is_safe_next(next_url):
                return redirect(next_url)
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html", next_url=next_url)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, role="admin", is_active=True).first()

        if user:
            token = password_reset_serializer(user).dumps(user.id)
            try:
                send_password_reset_email(user, token)
            except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
                app.logger.exception("Unable to send a password reset email.")

        flash(
            "If an active admin account uses that email, password reset instructions will be sent.",
            "success",
        )
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<int:user_id>/<token>", methods=["GET", "POST"])
def reset_password(user_id, token):
    user = db.session.get(User, user_id)
    if not user or not user.is_active or user.role != "admin" or not user.email:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("login"))

    try:
        token_user_id = password_reset_serializer(user).loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("forgot_password"))

    if token_user_id != user.id:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash("Your password has been reset. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password.html")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = {
        "full_name": current_user.full_name,
        "username": current_user.username,
        "email": current_user.email or "",
    }

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        form = {"full_name": full_name, "username": username, "email": email}

        error = None
        if not full_name or len(full_name) > 100:
            error = "Enter a full name of 1 to 100 characters."
        elif len(username) < 3 or len(username) > 50:
            error = "Username must be between 3 and 50 characters."
        elif User.query.filter(
            User.username == username, User.id != current_user.id
        ).first():
            error = "That username is already in use."
        elif not is_valid_email(email):
            error = "Please enter a valid email address."
        elif User.query.filter(
            User.email == email, User.id != current_user.id
        ).first():
            error = "That email address is already in use."
        elif current_password or new_password or confirm_password:
            if not current_password:
                error = "Enter your current password to change it."
            elif not check_password_hash(current_user.password_hash, current_password):
                error = "Your current password is incorrect."
            elif len(new_password) < 8:
                error = "New password must be at least 8 characters."
            elif new_password != confirm_password:
                error = "New passwords do not match."

        if error:
            flash(error, "error")
        else:
            current_user.full_name = full_name
            current_user.username = username
            current_user.email = email
            if new_password:
                current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Settings updated.", "success")
            return redirect(url_for("settings"))

    return render_template("settings.html", form=form)


@app.route("/admins", methods=["GET", "POST"])
@login_required
def admins():
    form = {"full_name": "", "username": "", "email": ""}

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        form = {"full_name": full_name, "username": username, "email": email}

        error = None
        if not full_name or len(full_name) > 100:
            error = "Enter a full name of 1 to 100 characters."
        elif len(username) < 3 or len(username) > 50:
            error = "Username must be between 3 and 50 characters."
        elif User.query.filter_by(username=username).first():
            error = "That username is already in use."
        elif not is_valid_email(email):
            error = "Please enter a valid email address."
        elif User.query.filter_by(email=email).first():
            error = "That email address is already in use."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if error:
            flash(error, "error")
        else:
            admin = User(
                username=username,
                full_name=full_name,
                email=email,
                password_hash=generate_password_hash(password),
                role="admin",
                is_active=True,
            )
            db.session.add(admin)
            db.session.commit()
            flash("Admin account created.", "success")
            return redirect(url_for("admins"))

    admin_list = User.query.filter_by(role="admin").order_by(User.full_name, User.id).all()
    return render_template("admins.html", form=form, admins=admin_list)


@app.route("/admins/<int:admin_id>/delete", methods=["POST"])
@login_required
def delete_admin(admin_id):
    admin = db.get_or_404(User, admin_id)
    if admin.role != "admin":
        flash("That account is not an admin.", "error")
    elif admin.id == current_user.id:
        flash("You cannot delete your own admin account.", "error")
    elif admin.is_active and User.query.filter_by(role="admin", is_active=True).count() <= 1:
        flash("You cannot delete the last active admin account.", "error")
    else:
        ProgramItem.query.filter_by(assigned_by=admin.id).update(
            {ProgramItem.assigned_by: None},
            synchronize_session=False,
        )
        db.session.delete(admin)
        db.session.commit()
        flash("Admin account deleted.", "success")
    return redirect(url_for("admins"))


@app.route("/account/email", methods=["GET", "POST"])
@login_required
def account_email():
    return redirect(url_for("settings"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# =====================================================================
# 6. DASHBOARD
# =====================================================================

@app.route("/dashboard")
@login_required
def dashboard():
    programs = (
        Program.query
        .filter(Program.program_date >= date.today())
        .order_by(Program.program_date)
        .limit(10)
        .all()
    )

    upcoming = [(program, count_unassigned(program)) for program in programs]

    return render_template("dashboard.html", upcoming=upcoming)


# =====================================================================
# 7. MEMBERS
# =====================================================================

def clean_name(raw):
    return " ".join(raw.split())[:100]


def find_member_by_name(full_name, exclude_id=None):
    query = Member.query.filter(db.func.lower(Member.full_name) == full_name.lower())
    if exclude_id is not None:
        query = query.filter(Member.id != exclude_id)
    return query.first()


@app.route("/members")
@login_required
def members():
    q = request.args.get("q", "").strip()

    query = Member.query
    if q:
        query = query.filter(Member.full_name.ilike(f"%{q}%"))

    all_members = query.order_by(Member.is_active.desc(), Member.full_name).all()
    active_count = Member.query.filter_by(is_active=True).count()

    return render_template(
        "members.html", members=all_members, q=q, active_count=active_count
    )


@app.route("/members/add", methods=["POST"])
@login_required
def add_member():
    full_name = clean_name(request.form.get("full_name", ""))
    display_name = request.form.get("display_name", "").strip()[:50] or None
    contact_number = request.form.get("contact_number", "").strip()[:30] or None

    if not full_name:
        flash("Please enter the member's full name.", "error")
    elif find_member_by_name(full_name):
        flash(f"{full_name} is already in the list.", "error")
    else:
        db.session.add(Member(
            full_name=full_name,
            display_name=display_name,
            contact_number=contact_number,
        ))
        db.session.commit()
        flash(f"{full_name} was added.", "success")

    return redirect(url_for("members"))


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
def edit_member(member_id):
    member = db.get_or_404(Member, member_id)

    form = {
        "full_name": member.full_name,
        "display_name": member.display_name or "",
        "contact_number": member.contact_number or "",
    }

    if request.method == "POST":
        form = {
            "full_name": clean_name(request.form.get("full_name", "")),
            "display_name": request.form.get("display_name", "").strip()[:50],
            "contact_number": request.form.get("contact_number", "").strip()[:30],
        }

        if not form["full_name"]:
            flash("Please enter the member's full name.", "error")
        elif find_member_by_name(form["full_name"], exclude_id=member.id):
            flash(f"{form['full_name']} is already in the list.", "error")
        else:
            member.full_name = form["full_name"]
            member.display_name = form["display_name"] or None
            member.contact_number = form["contact_number"] or None
            db.session.commit()
            flash("Member updated.", "success")
            return redirect(url_for("members"))

    return render_template("member_form.html", member=member, form=form)


@app.route("/members/<int:member_id>/toggle", methods=["POST"])
@login_required
def toggle_member(member_id):
    member = db.get_or_404(Member, member_id)
    member.is_active = not member.is_active
    db.session.commit()

    if member.is_active:
        flash(f"{member.full_name} is active again.", "success")
    else:
        flash(f"{member.full_name} was deactivated.", "success")
    return redirect(url_for("members"))


@app.route("/members/import", methods=["POST"])
@login_required
def import_members():
    raw = request.form.get("names", "")
    existing = {m.full_name.lower() for m in Member.query.all()}

    added = 0
    skipped = 0
    for line in raw.splitlines():
        name = clean_name(line)
        if not name:
            continue
        if name.lower() in existing:
            skipped += 1
            continue
        db.session.add(Member(full_name=name))
        existing.add(name.lower())
        added += 1

    db.session.commit()

    if added == 0 and skipped == 0:
        flash("No names found. Type one name per line.", "error")
    else:
        flash(f"Added {added} member(s). Skipped {skipped} already in the list.", "success")
    return redirect(url_for("members"))


@app.route("/songs", methods=["GET", "POST"])
@login_required
def songs():
    selected_song = None
    invalid_song_id = False
    song_id_text = request.form.get("song_id", request.args.get("edit", ""))
    if song_id_text:
        try:
            selected_song = db.get_or_404(Song, int(song_id_text))
        except ValueError:
            flash("Invalid song.", "error")
            invalid_song_id = True

    form = {
        "title": request.form.get("title", selected_song.title if selected_song else ""),
        "lyrics": request.form.get("lyrics", selected_song.lyrics if selected_song else ""),
    }
    if invalid_song_id:
        all_songs = Song.query.order_by(Song.title, Song.id).all()
        return render_template(
            "songs.html", songs=all_songs, selected_song=None, form=form
        )

    if request.method == "POST":
        title = form["title"].strip()
        lyrics = form["lyrics"].strip()
        if not title or not lyrics:
            flash("Please enter both the song title and lyrics.", "error")
        elif len(title) > 150:
            flash("Song titles must be 150 characters or fewer.", "error")
        elif len(lyrics) > 50000:
            flash("Lyrics must be 50,000 characters or fewer.", "error")
        else:
            song = selected_song or Song()
            song.title = title
            song.lyrics = lyrics
            if selected_song is None:
                db.session.add(song)
            db.session.commit()
            flash("Song saved.", "success")
            return redirect(url_for("songs"))

    all_songs = Song.query.order_by(Song.title, Song.id).all()
    return render_template(
        "songs.html", songs=all_songs, selected_song=selected_song, form=form
    )


@app.route("/songs/<int:song_id>")
@login_required
def song_lyrics(song_id):
    song = db.get_or_404(Song, song_id)
    return render_template("song_lyrics.html", song=song)


# =====================================================================
# 8. PROGRAMS
# =====================================================================

PROGRAM_STATUSES = ["draft", "published", "done", "cancelled"]


@app.route("/programs")
@login_required
def programs():
    today = date.today()

    upcoming = (
        Program.query
        .filter(Program.program_date >= today)
        .order_by(Program.program_date, Program.program_type_id)
        .all()
    )

    past = (
        Program.query
        .filter(Program.program_date < today)
        .order_by(Program.program_date.desc())
        .limit(30)
        .all()
    )

    return render_template(
        "programs.html",
        program_types=ProgramType.query.order_by(ProgramType.id).all(),
        upcoming=[(p, count_unassigned(p)) for p in upcoming],
        past=[(p, count_unassigned(p)) for p in past],
    )


@app.route("/program-types/<int:program_type_id>/template", methods=["GET", "POST"])
@login_required
def edit_program_template(program_type_id):
    program_type = db.get_or_404(ProgramType, program_type_id)
    available_parts = PartType.query.order_by(PartType.name).all()
    categories = ["song", "prayer", "talk", "report", "study", "other"]
    form_rows = [
        {
            "part_type_id": str(template_part.part_type_id),
            "part_name": template_part.part_type.name,
            "custom_name": "",
            "custom_category": "other",
            "custom_needs_detail": "no",
        }
        for template_part in program_type.template_parts
    ]

    if request.method == "POST":
        part_type_ids = request.form.getlist("part_type_id")
        custom_names = request.form.getlist("custom_name")
        custom_categories = request.form.getlist("custom_category")
        custom_details = request.form.getlist("custom_needs_detail")
        form_rows = []
        selections = []
        error = None

        if not (
            len(part_type_ids)
            == len(custom_names)
            == len(custom_categories)
            == len(custom_details)
        ):
            error = "The program template could not be read. Please try again."
        else:
            seen_parts = set()
            seen_names = set()
            parts_by_id = {str(part.id): part for part in available_parts}

            for part_type_id_value, custom_name_value, category_value, detail_value in zip(
                part_type_ids, custom_names, custom_categories, custom_details
            ):
                custom_name = " ".join(custom_name_value.split())
                form_rows.append({
                    "part_type_id": part_type_id_value,
                    "part_name": (
                        parts_by_id[part_type_id_value].name
                        if part_type_id_value in parts_by_id
                        else custom_name
                    ),
                    "custom_name": custom_name_value,
                    "custom_category": category_value,
                    "custom_needs_detail": detail_value,
                })

                if part_type_id_value == "custom":
                    normalized_name = custom_name.casefold()
                    if not custom_name or len(custom_name) > 50:
                        error = "Custom part names must be between 1 and 50 characters."
                        break
                    if category_value not in categories:
                        error = "Choose a valid category for the custom part."
                        break
                    if detail_value not in ("yes", "no"):
                        error = "Choose whether the custom part needs details."
                        break
                    if PartType.query.filter(
                        db.func.lower(PartType.name) == normalized_name
                    ).first():
                        error = "A part with that name already exists. Choose it from the list instead."
                        break
                    if normalized_name in seen_names:
                        error = "Each part can only appear once in a program template."
                        break

                    seen_names.add(normalized_name)
                    selections.append((
                        "custom",
                        custom_name,
                        category_value,
                        detail_value == "yes",
                    ))
                    continue

                part = parts_by_id.get(part_type_id_value)
                if not part:
                    error = "Choose a valid program part."
                    break
                if part.id in seen_parts:
                    error = "Each part can only appear once in a program template."
                    break

                seen_parts.add(part.id)
                selections.append(("existing", part))

        if error:
            flash(error, "error")
        else:
            ordered_parts = []
            for selection in selections:
                if selection[0] == "existing":
                    part = selection[1]
                else:
                    _, name, category, needs_detail = selection
                    part = PartType(
                        name=name,
                        category=category,
                        needs_detail=needs_detail,
                    )
                    db.session.add(part)
                    db.session.flush()
                ordered_parts.append(part)

            for template_part in program_type.template_parts:
                db.session.delete(template_part)
            for sort_order, part in enumerate(ordered_parts, start=1):
                db.session.add(ProgramTypePart(
                    program_type_id=program_type.id,
                    part_type_id=part.id,
                    sort_order=sort_order,
                ))

            db.session.commit()
            flash("Program template updated. This applies to programs created from now on.", "success")
            return redirect(url_for("programs"))

    return render_template(
        "program_template.html",
        program_type=program_type,
        available_parts=available_parts,
        categories=categories,
        form_rows=form_rows,
    )


@app.route("/calendar", methods=["GET", "POST"])
@login_required
def calendar():
    today = date.today()

    if request.method == "POST":
        month_text = request.form.get("month", "")
        try:
            assignment_month = datetime.strptime(month_text, "%Y-%m").date()
        except ValueError:
            flash("Please choose a valid month.", "error")
            return redirect(url_for("calendar"))

        elder_name = " ".join(request.form.get("elder_name", "").split())
        if len(elder_name) > 100:
            flash("The elder name must be 100 characters or fewer.", "error")
            return redirect(url_for(
                "calendar",
                month=assignment_month.strftime("%Y-%m"),
                _anchor="monthly-elder",
            ))

        assignment = MonthlyElderAssignment.query.filter_by(
            year=assignment_month.year,
            month=assignment_month.month,
        ).first()
        if elder_name:
            if assignment:
                assignment.elder_name = elder_name
            else:
                db.session.add(MonthlyElderAssignment(
                    year=assignment_month.year,
                    month=assignment_month.month,
                    elder_name=elder_name,
                ))
            db.session.commit()
            flash("Monthly elder assignment saved.", "success")
        elif assignment:
            db.session.delete(assignment)
            db.session.commit()
            flash("Monthly elder assignment removed.", "success")
        else:
            flash("Enter an elder name to save an assignment.", "error")

        return redirect(url_for(
            "calendar",
            month=assignment_month.strftime("%Y-%m"),
            _anchor="monthly-elder",
        ))

    date_text = request.args.get("date", "")
    month_text = request.args.get("month", "")

    if date_text:
        try:
            selected_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            flash("Please choose a valid date.", "error")
            selected_date = today
    elif month_text:
        try:
            selected_date = datetime.strptime(month_text, "%Y-%m").date()
        except ValueError:
            flash("Please choose a valid month.", "error")
            selected_date = today
    else:
        selected_date = today

    month_start = selected_date.replace(day=1)
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)

    if month_start.month == 1:
        previous_month = date(month_start.year - 1, 12, 1)
    else:
        previous_month = date(month_start.year, month_start.month - 1, 1)

    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(
        month_start.year, month_start.month
    )
    month_programs = (
        Program.query
        .filter(
            Program.program_date >= month_start,
            Program.program_date < next_month,
        )
        .order_by(Program.program_date, Program.program_type_id)
        .all()
    )
    programs_by_date = {}
    for program in month_programs:
        programs_by_date.setdefault(program.program_date.isoformat(), []).append(program)

    selected_programs = (
        Program.query
        .filter_by(program_date=selected_date)
        .order_by(Program.program_type_id)
        .all()
    )
    monthly_elder = MonthlyElderAssignment.query.filter_by(
        year=month_start.year,
        month=month_start.month,
    ).first()

    return render_template(
        "calendar.html",
        weeks=weeks,
        selected_date=selected_date,
        month_start=month_start,
        previous_month=previous_month,
        next_month=next_month,
        programs_by_date=programs_by_date,
        selected_programs=selected_programs,
        monthly_elder=monthly_elder,
    )


@app.route("/programs/new", methods=["GET", "POST"])
@login_required
def new_program():
    types = ProgramType.query.order_by(ProgramType.id).all()
    default_dates = {t.id: next_date_for_weekday(t.weekday).isoformat() for t in types}

    type_id = request.args.get("type", type=int) or types[0].id
    date_given = bool(request.args.get("date"))
    form = {
        "program_type_id": type_id,
        "program_date": request.args.get("date") or default_dates.get(type_id, ""),
        "weeks": 1,
    }

    if request.method == "POST":
        type_id = request.form.get("program_type_id", type=int)
        date_text = request.form.get("program_date", "")
        weeks = request.form.get("weeks", default=1, type=int)
        weeks = max(1, min(weeks, 12))

        form = {"program_type_id": type_id, "program_date": date_text, "weeks": weeks}
        date_given = True

        program_type = db.session.get(ProgramType, type_id) if type_id else None

        try:
            start_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            start_date = None

        if not program_type:
            flash("Please choose a program type.", "error")
        elif not start_date:
            flash("Please choose a valid date.", "error")
        elif not program_type.template_parts:
            flash("Add parts to this program template before creating a program.", "error")
            return redirect(url_for(
                "edit_program_template",
                program_type_id=program_type.id,
            ))
        else:
            created = []
            skipped = 0
            for week in range(weeks):
                program = create_program_from_template(
                    program_type.id, start_date + timedelta(weeks=week)
                )
                if program:
                    created.append(program)
                else:
                    skipped += 1

            if not created:
                flash("No new programs were created. They already exist for the selected dates.", "error")
            elif len(created) == 1 and skipped == 0:
                flash("Program created.", "success")
                return redirect(url_for("program_detail", program_id=created[0].id))
            else:
                message = f"Created {len(created)} program(s)."
                if skipped:
                    message += f" Skipped {skipped} that already existed."
                flash(message, "success")
                return redirect(url_for("programs"))

    return render_template(
        "program_new.html",
        types=types, form=form,
        default_dates=default_dates, date_given=date_given,
    )


@app.route("/programs/<int:program_id>")
@login_required
def program_detail(program_id):
    program = db.get_or_404(Program, program_id)
    return render_template(
        "program_detail.html",
        program=program,
        statuses=PROGRAM_STATUSES,
        unassigned=count_unassigned(program),
    )


@app.route("/programs/<int:program_id>/edit", methods=["GET", "POST"])
@login_required
def edit_program(program_id):
    program = db.get_or_404(Program, program_id)
    members = (
        Member.query
        .filter_by(is_active=True)
        .order_by(Member.full_name)
        .all()
    )
    member_options = [{"id": member.id, "label": member.label} for member in members]
    song_options = [
        {"id": song.id, "title": song.title}
        for song in Song.query.order_by(Song.title, Song.id).all()
    ]

    if request.method == "POST":
        item_by_id = {item.id: item for item in program.items}
        submitted_ids = request.form.getlist("item_id")

        try:
            ordered_ids = [int(item_id) for item_id in submitted_ids]
        except ValueError:
            ordered_ids = []

        if (
            len(ordered_ids) != len(item_by_id)
            or len(set(ordered_ids)) != len(ordered_ids)
            or set(ordered_ids) != set(item_by_id)
        ):
            flash("The program parts could not be read. Please try again.", "error")
            return render_template(
                "program_editor.html", program=program, members=members,
                member_options=member_options, song_options=song_options,
            )

        updates = []
        error = None
        for item_id in ordered_ids:
            item = item_by_id[item_id]
            member_text = request.form.get(f"member_id_{item_id}", "").strip()
            guest_name = request.form.get(f"guest_name_{item_id}", "").strip()
            is_song_part = item.part_type.category == "song"
            song_title = request.form.get(f"song_title_{item_id}", "").strip()
            song_text = request.form.get(f"song_id_{item_id}", "").strip()
            detail = (
                song_title if is_song_part and song_text == ""
                else request.form.get(f"detail_{item_id}", "").strip()
            )

            if len(guest_name) > 100 or (not is_song_part and len(detail) > 200):
                error = "Guest names must be 100 characters or fewer and details 200 characters or fewer."
                break
            if is_song_part and len(song_title) > 150:
                error = "Song titles must be 150 characters or fewer."
                break

            try:
                song_id = int(song_text) if song_text else None
            except ValueError:
                error = "Please choose a valid song."
                break

            song = db.session.get(Song, song_id) if song_id else None
            if song_id is not None and (
                not is_song_part or not song or song.title != song_title
            ):
                error = "Please choose a valid song from the suggestions."
                break
            if (
                is_song_part and song_title and song_id is None
                and song_title != item.detail
            ):
                error = "Select a saved song from the suggestions, or add it on the Songs page."
                break

            try:
                member_id = int(member_text) if member_text else None
            except ValueError:
                error = "Please choose a valid member."
                break

            member = db.session.get(Member, member_id) if member_id else None
            if member_id is not None and (
                not member
                or (not member.is_active and item.member_id != member.id)
            ):
                error = "Please choose an active member."
                break
            if member_id is not None and guest_name:
                error = "Choose either a member or a guest for each part, not both."
                break

            updates.append((
                item, member_id, guest_name or None, detail or None, song_id
            ))

        if error:
            flash(error, "error")
            return render_template(
                "program_editor.html", program=program, members=members,
                member_options=member_options, song_options=song_options,
            )

        for sort_order, (item, member_id, guest_name, detail, song_id) in enumerate(updates, start=1):
            if (item.member_id, item.guest_name) != (member_id, guest_name):
                is_assigned = member_id is not None or guest_name is not None
                item.assigned_by = current_user.id if is_assigned else None
                item.assigned_at = datetime.utcnow() if is_assigned else None
            item.member_id = member_id
            item.guest_name = guest_name
            item.detail = detail
            item.song_id = song_id
            item.sort_order = sort_order

        db.session.commit()
        flash("Program updated.", "success")
        return redirect(url_for("program_detail", program_id=program.id))

    return render_template(
        "program_editor.html", program=program, members=members,
        member_options=member_options, song_options=song_options,
    )


@app.route("/programs/<int:program_id>/status", methods=["POST"])
@login_required
def update_program_status(program_id):
    program = db.get_or_404(Program, program_id)
    status = request.form.get("status", "")

    if status in PROGRAM_STATUSES:
        program.status = status
        db.session.commit()
        flash(f"Status changed to {status}.", "success")
    else:
        flash("Invalid status.", "error")
    return redirect(url_for("program_detail", program_id=program.id))


@app.route("/programs/<int:program_id>/delete", methods=["POST"])
@login_required
def delete_program(program_id):
    program = db.get_or_404(Program, program_id)
    db.session.delete(program)
    db.session.commit()
    flash("Program deleted.", "success")
    return redirect(url_for("programs"))


# =====================================================================
# 9. SIMULAN ANG APP
# =====================================================================

with app.app_context():
    db.create_all()
    user_columns = {
        column["name"] for column in inspect(db.engine).get_columns("users")
    }
    if "email" not in user_columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN email VARCHAR(254)")
    program_item_columns = {
        column["name"] for column in inspect(db.engine).get_columns("program_items")
    }
    if "song_id" not in program_item_columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE program_items ADD COLUMN song_id INTEGER REFERENCES songs(id)"
            )
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email)"
        )
    seed_defaults()

if __name__ == "__main__":
    app.run(debug=True)