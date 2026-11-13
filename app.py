# =====================================================================
# CHURCH PROGRAM SCHEDULING SYSTEM
# app.py  -  BAHAGI 4: Setup, Login, Dashboard, Members, at Programs
# =====================================================================

import os
import calendar as calendar_module
import re
import secrets
import smtplib
from functools import wraps
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask, abort, render_template, redirect, url_for, request, flash, session
)
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from sqlalchemy import inspect, text
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

class Church(db.Model):
    __tablename__ = "churches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    district = db.Column(db.String(100), nullable=False)
    registration_code = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class User(UserMixin, db.Model):
    """Account ng admin (ang may login)."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)    # hindi kailanman plain na password
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254))
    role = db.Column(db.String(20), default="church_admin", nullable=False)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"))
    church = db.relationship("Church")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)


class Member(db.Model):
    """Mga miyembro ng church na pwedeng bigyan ng bahagi."""
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
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
    display_name = db.Column(db.String(50))
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"))
    weekday = db.Column(db.Integer, nullable=False)

    template_parts = db.relationship(
        "ProgramTypePart",
        backref="program_type",
        order_by="ProgramTypePart.sort_order",
        cascade="all, delete-orphan",
    )

    @property
    def label(self):
        return self.display_name or self.name


class PartType(db.Model):
    """Katalogo ng mga bahagi (Opening song, Gaceta, atbp.)."""
    __tablename__ = "part_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    display_name = db.Column(db.String(50))
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"))
    category = db.Column(db.String(20))                   # song, prayer, talk, report, study
    needs_detail = db.Column(db.Boolean, default=False)   # kailangan ba ng pamagat/paksa

    @property
    def label(self):
        return self.display_name or self.name


class Song(db.Model):
    """Kanta at lyrics na puwedeng iugnay sa mga bahagi ng programa."""
    __tablename__ = "songs"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    lyrics = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProgramTypePart(db.Model):
    """Template: aling bahagi ang kasama sa aling programa, at sa anong pagkakasunod."""
    __tablename__ = "program_type_parts"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"))
    program_type_id = db.Column(db.Integer, db.ForeignKey("program_types.id"), nullable=False)
    part_type_id = db.Column(db.Integer, db.ForeignKey("part_types.id"), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)
    is_required = db.Column(db.Boolean, default=True)

    part_type = db.relationship("PartType")


class MonthlyElderAssignment(db.Model):
    """Pangalan ng elder na naka-assign para sa isang partikular na buwan."""
    __tablename__ = "monthly_elder_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "church_id", "year", "month", name="uq_monthly_elder_church_year_month"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    elder_name = db.Column(db.String(100), nullable=False)


class Program(db.Model):
    """Isang aktwal na programa sa isang petsa."""
    __tablename__ = "programs"
    __table_args__ = (
        db.UniqueConstraint(
            "church_id", "program_type_id", "program_date",
            name="uq_program_church_type_date",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    program_type_id = db.Column(db.Integer, db.ForeignKey("program_types.id"), nullable=False)
    program_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="draft")    # draft, published, done, cancelled
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    church = db.relationship("Church")
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


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    author = db.relationship("User")


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
            part = PartType(
                name=name,
                display_name=name,
                church_id=None,
                category=category,
                needs_detail=needs_detail,
            )
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
        program_type = ProgramType.query.filter_by(
            name=name, church_id=None
        ).first()
        is_new_type = program_type is None
        if not program_type:
            program_type = ProgramType(
                name=name, display_name=name, weekday=weekday, church_id=None
            )
            db.session.add(program_type)
            db.session.flush()

        if not is_new_type:
            continue

        for order, part_name in enumerate(part_names, start=1):
            db.session.add(ProgramTypePart(
                church_id=None,
                program_type=program_type,
                part_type=parts[part_name],
                sort_order=order,
            ))

    db.session.commit()


# =====================================================================
# 4. TULONG NA MGA FUNCTION
# =====================================================================

def create_program_from_template(program_type_id, program_date, church_id):
    existing = Program.query.filter_by(
        church_id=church_id,
        program_type_id=program_type_id,
        program_date=program_date,
    ).first()
    if existing:
        return None

    program = Program(
        church_id=church_id,
        program_type_id=program_type_id,
        program_date=program_date,
    )
    db.session.add(program)

    program_type = db.session.get(ProgramType, program_type_id)
    for template_part in program_type.template_parts:
        program.items.append(ProgramItem(
            part_type_id=template_part.part_type_id,
            sort_order=template_part.sort_order,
        ))

    db.session.commit()
    return program


def mission_admin_exists():
    return User.query.filter_by(role="mission_admin").first() is not None


def mission_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "mission_admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def church_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "church_admin" or not current_user.church_id:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def get_local_or_404(model, record_id):
    return model.query.filter_by(
        id=record_id, church_id=current_user.church_id
    ).first_or_404()


def local_program_types():
    return ProgramType.query.filter_by(
        church_id=current_user.church_id
    ).order_by(ProgramType.id)


def copy_default_templates(church):
    defaults = ProgramType.query.filter_by(church_id=None).order_by(ProgramType.id).all()
    for default in defaults:
        local_type = ProgramType(
            name=f"{default.name[:35]} [{church.id}]",
            display_name=default.label,
            church_id=church.id,
            weekday=default.weekday,
        )
        db.session.add(local_type)
        db.session.flush()
        for template_part in default.template_parts:
            db.session.add(ProgramTypePart(
                church_id=church.id,
                program_type_id=local_type.id,
                part_type_id=template_part.part_type_id,
                sort_order=template_part.sort_order,
                is_required=template_part.is_required,
            ))


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
    if not mission_admin_exists() and User.query.count() == 0:
        return redirect(url_for("setup"))
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if User.query.count() > 0:
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
                role="mission_admin",
            )
            db.session.add(admin)
            db.session.commit()
            flash("Mission Admin account created. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("setup.html", form=form)


@app.route("/register", methods=["GET", "POST"])
def register():
    churches = Church.query.order_by(Church.district, Church.name).all()
    form = {
        "full_name": "",
        "username": "",
        "email": "",
        "church_id": "",
    }

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        church_id = request.form.get("church_id", type=int)
        code = request.form.get("registration_code", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        form = {
            "full_name": full_name,
            "username": username,
            "email": email,
            "church_id": str(church_id or ""),
        }
        church = db.session.get(Church, church_id) if church_id else None

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
        elif not church or not secrets.compare_digest(
            code.casefold(), church.registration_code.casefold()
        ):
            error = "Choose your church and enter its valid registration code."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if error:
            flash(error, "error")
        else:
            user = User(
                username=username,
                full_name=full_name,
                email=email,
                password_hash=generate_password_hash(password),
                role="church_admin",
                church_id=church.id,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            flash("Superintendent account created. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("register.html", churches=churches, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
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
        user = User.query.filter(
            User.email == email,
            User.role.in_(("mission_admin", "church_admin")),
            User.is_active.is_(True),
        ).first()

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
    if not user or not user.is_active or user.role not in (
        "mission_admin", "church_admin"
    ) or not user.email:
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
@mission_admin_required
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
                role="mission_admin",
                is_active=True,
            )
            db.session.add(admin)
            db.session.commit()
            flash("Mission Admin account created.", "success")
            return redirect(url_for("admins"))

    admin_list = User.query.filter_by(
        role="mission_admin"
    ).order_by(User.full_name, User.id).all()
    return render_template("admins.html", form=form, admins=admin_list)


@app.route("/admins/<int:admin_id>/delete", methods=["POST"])
@mission_admin_required
def delete_admin(admin_id):
    admin = db.get_or_404(User, admin_id)
    if admin.role != "mission_admin":
        flash("That account is not a Mission Admin.", "error")
    elif admin.id == current_user.id:
        flash("You cannot delete your own Mission Admin account.", "error")
    elif admin.is_active and User.query.filter_by(
        role="mission_admin", is_active=True
    ).count() <= 1:
        flash("You cannot delete the last active Mission Admin account.", "error")
    else:
        Announcement.query.filter_by(created_by=admin.id).update(
            {Announcement.created_by: current_user.id},
            synchronize_session=False,
        )
        ProgramItem.query.filter_by(assigned_by=admin.id).update(
            {ProgramItem.assigned_by: None},
            synchronize_session=False,
        )
        db.session.delete(admin)
        db.session.commit()
        flash("Admin account deleted.", "success")
    return redirect(url_for("admins"))


def validate_church_form(church_id=None):
    name = " ".join(request.form.get("name", "").split())
    district = " ".join(request.form.get("district", "").split())
    code = request.form.get("registration_code", "").strip().upper()
    error = None

    if not name or len(name) > 120:
        error = "Church names must be between 1 and 120 characters."
    elif not district or len(district) > 100:
        error = "District names must be between 1 and 100 characters."
    elif not code or len(code) > 100:
        error = "Registration codes must be between 1 and 100 characters."
    elif Church.query.filter(
        db.func.lower(Church.registration_code) == code.lower(),
        Church.id != (church_id or -1),
    ).first():
        error = "That registration code is already assigned to a church."
    return name, district, code, error


@app.route("/churches", methods=["GET", "POST"])
@mission_admin_required
def churches():
    if request.method == "POST":
        name, district, code, error = validate_church_form()
        if error:
            flash(error, "error")
        else:
            church = Church(
                name=name,
                district=district,
                registration_code=code,
            )
            db.session.add(church)
            db.session.flush()
            copy_default_templates(church)
            db.session.commit()
            flash(f"{church.name} was added.", "success")
            return redirect(url_for("churches"))

    all_churches = Church.query.order_by(Church.district, Church.name).all()
    return render_template("churches.html", churches=all_churches)


@app.route("/churches/<int:church_id>/edit", methods=["GET", "POST"])
@mission_admin_required
def edit_church(church_id):
    church = db.get_or_404(Church, church_id)
    if request.method == "POST":
        name, district, code, error = validate_church_form(church.id)
        if error:
            flash(error, "error")
        else:
            church.name = name
            church.district = district
            church.registration_code = code
            db.session.commit()
            flash("Church details updated.", "success")
            return redirect(url_for("churches"))
    return render_template(
        "churches.html",
        churches=Church.query.order_by(Church.district, Church.name).all(),
        editing=church,
    )


@app.route("/churches/<int:church_id>/delete", methods=["POST"])
@mission_admin_required
def delete_church(church_id):
    church = db.get_or_404(Church, church_id)
    church_name = church.name
    program_ids = db.session.query(Program.id).filter_by(church_id=church.id)
    church_user_ids = db.session.query(User.id).filter_by(church_id=church.id)
    ProgramItem.query.filter(
        ProgramItem.assigned_by.in_(church_user_ids)
    ).update({ProgramItem.assigned_by: None}, synchronize_session=False)
    db.session.query(ProgramItem).filter(
        ProgramItem.program_id.in_(program_ids)
    ).delete(synchronize_session=False)
    Program.query.filter_by(church_id=church.id).delete(synchronize_session=False)
    ProgramTypePart.query.filter_by(church_id=church.id).delete(
        synchronize_session=False
    )
    ProgramType.query.filter_by(church_id=church.id).delete(
        synchronize_session=False
    )
    PartType.query.filter_by(church_id=church.id).delete(synchronize_session=False)
    Member.query.filter_by(church_id=church.id).delete(synchronize_session=False)
    Song.query.filter_by(church_id=church.id).delete(synchronize_session=False)
    MonthlyElderAssignment.query.filter_by(church_id=church.id).delete(
        synchronize_session=False
    )
    User.query.filter_by(church_id=church.id).delete(synchronize_session=False)
    db.session.delete(church)
    db.session.commit()
    flash(f"{church_name} and its local data were deleted.", "success")
    return redirect(url_for("churches"))


@app.route("/announcements", methods=["POST"])
@mission_admin_required
def create_announcement():
    announcement_id_text = request.form.get("announcement_id", "").strip()
    try:
        announcement_id = int(announcement_id_text) if announcement_id_text else None
    except ValueError:
        flash("Invalid announcement.", "error")
        return redirect(url_for("dashboard"))
    title = " ".join(request.form.get("title", "").split())
    content = request.form.get("content", "").strip()
    if not title or len(title) > 150 or not content or len(content) > 10000:
        flash("Enter a title (up to 150 characters) and content (up to 10,000 characters).", "error")
    else:
        if announcement_id:
            announcement = db.get_or_404(Announcement, announcement_id)
            announcement.title = title
            announcement.content = content
        else:
            announcement = Announcement(
                title=title,
                content=content,
                created_by=current_user.id,
                is_active=True,
            )
            db.session.add(announcement)
        db.session.commit()
        flash(
            "Mission announcement updated." if announcement_id
            else "Mission announcement posted.",
            "success",
        )
    return redirect(url_for("dashboard"))


@app.route("/announcements/<int:announcement_id>/toggle", methods=["POST"])
@mission_admin_required
def toggle_announcement(announcement_id):
    announcement = db.get_or_404(Announcement, announcement_id)
    announcement.is_active = not announcement.is_active
    db.session.commit()
    flash("Announcement visibility updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
@mission_admin_required
def delete_announcement(announcement_id):
    announcement = db.get_or_404(Announcement, announcement_id)
    db.session.delete(announcement)
    db.session.commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("dashboard"))


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
    if current_user.role not in ("mission_admin", "church_admin"):
        abort(403)
    if current_user.role == "mission_admin":
        district, church_id, status = mission_filter_values()
        all_churches = Church.query.order_by(Church.district, Church.name).all()
        filtered_query = apply_program_filters(
            Program.query.filter(Program.program_date >= date.today()),
            district, church_id, status,
        )
        upcoming = filtered_query.order_by(
            Program.program_date, Program.id
        ).all()
        all_upcoming = Program.query.filter(
            Program.program_date >= date.today()
        ).all()
        attention_count = sum(count_unassigned(program) > 0 for program in all_upcoming)
        scheduled_church_ids = {program.church_id for program in all_upcoming}
        district_summary = []
        for district_name in sorted({church.district for church in all_churches}):
            district_churches = [
                church for church in all_churches if church.district == district_name
            ]
            district_summary.append({
                "name": district_name,
                "churches": district_churches,
                "scheduled": sum(
                    church.id in scheduled_church_ids for church in district_churches
                ),
                "scheduled_ids": {
                    church.id for church in district_churches
                    if church.id in scheduled_church_ids
                },
            })
        return render_template(
            "mission_dashboard.html",
            churches=all_churches,
            districts=sorted({church.district for church in all_churches}),
            district_filter=district,
            church_filter=church_id,
            status_filter=status,
            upcoming=[(program, count_unassigned(program)) for program in upcoming],
            total_programs=len(all_upcoming),
            attention_count=attention_count,
            announcements=Announcement.query.order_by(
                Announcement.is_active.desc(), Announcement.created_at.desc()
            ).all(),
            district_summary=district_summary,
        )

    programs = (
        Program.query
        .filter(
            Program.church_id == current_user.church_id,
            Program.program_date >= date.today(),
        )
        .order_by(Program.program_date)
        .limit(10)
        .all()
    )

    upcoming = [(program, count_unassigned(program)) for program in programs]
    return render_template(
        "dashboard.html",
        upcoming=upcoming,
        announcements=Announcement.query.filter_by(is_active=True).order_by(
            Announcement.created_at.desc()
        ).all(),
    )


def mission_filter_values():
    district = request.args.get("district", "").strip()
    church_text = request.args.get("church_id", "").strip()
    status = request.args.get("status", "all").strip().lower()
    if status not in {"all", "published", "draft"}:
        abort(400, description="Choose a valid program status filter.")
    if church_text:
        try:
            church_id = int(church_text)
        except ValueError:
            abort(400, description="Choose a valid church.")
        if not db.session.get(Church, church_id):
            abort(400, description="Choose a valid church.")
    else:
        church_id = None
    return district, church_id, status


def apply_program_filters(query, district, church_id, status):
    if current_user.role == "church_admin":
        query = query.filter(Program.church_id == current_user.church_id)
    elif current_user.role == "mission_admin":
        if district:
            query = query.join(Church).filter(Church.district == district)
        if church_id:
            query = query.filter(Program.church_id == church_id)
        if status != "all":
            query = query.filter(Program.status == status)
    else:
        abort(403)
    return query


# =====================================================================
# 7. MEMBERS
# =====================================================================

def clean_name(raw):
    return " ".join(raw.split())[:100]


def find_member_by_name(full_name, exclude_id=None):
    query = Member.query.filter(
        Member.church_id == current_user.church_id,
        db.func.lower(Member.full_name) == full_name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(Member.id != exclude_id)
    return query.first()


@app.route("/members")
@church_admin_required
def members():
    q = request.args.get("q", "").strip()

    query = Member.query.filter_by(church_id=current_user.church_id)
    if q:
        query = query.filter(Member.full_name.ilike(f"%{q}%"))

    all_members = query.order_by(Member.is_active.desc(), Member.full_name).all()
    active_count = Member.query.filter_by(
        church_id=current_user.church_id, is_active=True
    ).count()

    return render_template(
        "members.html", members=all_members, q=q, active_count=active_count
    )


@app.route("/members/add", methods=["POST"])
@church_admin_required
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
            church_id=current_user.church_id,
            full_name=full_name,
            display_name=display_name,
            contact_number=contact_number,
        ))
        db.session.commit()
        flash(f"{full_name} was added.", "success")

    return redirect(url_for("members"))


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@church_admin_required
def edit_member(member_id):
    member = get_local_or_404(Member, member_id)

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
@church_admin_required
def toggle_member(member_id):
    member = get_local_or_404(Member, member_id)
    member.is_active = not member.is_active
    db.session.commit()

    if member.is_active:
        flash(f"{member.full_name} is active again.", "success")
    else:
        flash(f"{member.full_name} was deactivated.", "success")
    return redirect(url_for("members"))


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@church_admin_required
def delete_member(member_id):
    member = get_local_or_404(Member, member_id)
    member_name = member.full_name
    ProgramItem.query.filter_by(member_id=member.id).update(
        {
            ProgramItem.member_id: None,
            ProgramItem.assigned_by: None,
            ProgramItem.assigned_at: None,
        },
        synchronize_session=False,
    )
    db.session.delete(member)
    db.session.commit()
    flash(f"{member_name} was removed from this church's member list.", "success")
    return redirect(url_for("members"))


@app.route("/members/import", methods=["POST"])
@church_admin_required
def import_members():
    raw = request.form.get("names", "")
    existing = {
        m.full_name.lower()
        for m in Member.query.filter_by(church_id=current_user.church_id).all()
    }

    added = 0
    skipped = 0
    for line in raw.splitlines():
        name = clean_name(line)
        if not name:
            continue
        if name.lower() in existing:
            skipped += 1
            continue
        db.session.add(Member(
            church_id=current_user.church_id, full_name=name
        ))
        existing.add(name.lower())
        added += 1

    db.session.commit()

    if added == 0 and skipped == 0:
        flash("No names found. Type one name per line.", "error")
    else:
        flash(f"Added {added} member(s). Skipped {skipped} already in the list.", "success")
    return redirect(url_for("members"))


@app.route("/songs", methods=["GET", "POST"])
@church_admin_required
def songs():
    selected_song = None
    invalid_song_id = False
    song_id_text = request.form.get("song_id", request.args.get("edit", ""))
    if song_id_text:
        try:
            selected_song = get_local_or_404(Song, int(song_id_text))
        except ValueError:
            flash("Invalid song.", "error")
            invalid_song_id = True

    form = {
        "title": request.form.get("title", selected_song.title if selected_song else ""),
        "lyrics": request.form.get("lyrics", selected_song.lyrics if selected_song else ""),
    }
    if invalid_song_id:
        all_songs = Song.query.filter_by(
            church_id=current_user.church_id
        ).order_by(Song.title, Song.id).all()
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
            song = selected_song or Song(church_id=current_user.church_id)
            song.title = title
            song.lyrics = lyrics
            if selected_song is None:
                db.session.add(song)
            db.session.commit()
            flash("Song saved.", "success")
            return redirect(url_for("songs"))

    all_songs = Song.query.filter_by(
        church_id=current_user.church_id
    ).order_by(Song.title, Song.id).all()
    return render_template(
        "songs.html", songs=all_songs, selected_song=selected_song, form=form
    )


@app.route("/songs/<int:song_id>")
@church_admin_required
def song_lyrics(song_id):
    song = get_local_or_404(Song, song_id)
    return render_template("song_lyrics.html", song=song)


# =====================================================================
# 8. PROGRAMS
# =====================================================================

PROGRAM_STATUSES = ["draft", "published", "done", "cancelled"]


@app.route("/programs")
@login_required
def programs():
    today = date.today()
    if current_user.role == "mission_admin":
        district, church_id, status = mission_filter_values()
        program_types = []
    else:
        district, church_id, status = "", None, "all"
        program_types = local_program_types().all()

    upcoming = (
        apply_program_filters(
            Program.query.filter(Program.program_date >= today),
            district, church_id, status,
        )
        .order_by(Program.program_date, Program.program_type_id)
        .all()
    )

    past = (
        apply_program_filters(
            Program.query.filter(Program.program_date < today),
            district, church_id, status,
        )
        .order_by(Program.program_date.desc())
        .limit(30)
        .all()
    )

    return render_template(
        "programs.html",
        program_types=program_types,
        upcoming=[(p, count_unassigned(p)) for p in upcoming],
        past=[(p, count_unassigned(p)) for p in past],
        churches=Church.query.order_by(Church.district, Church.name).all()
        if current_user.role == "mission_admin" else [],
        districts=sorted({
            church.district for church in Church.query.all()
        }) if current_user.role == "mission_admin" else [],
        district_filter=district,
        church_filter=church_id,
        status_filter=status,
    )


@app.route("/program-types/<int:program_type_id>/template", methods=["GET", "POST"])
@church_admin_required
def edit_program_template(program_type_id):
    program_type = ProgramType.query.filter_by(
        id=program_type_id, church_id=current_user.church_id
    ).first_or_404()
    available_parts = PartType.query.filter(
        db.or_(
            PartType.church_id.is_(None),
            PartType.church_id == current_user.church_id,
        )
    ).order_by(PartType.display_name, PartType.name).all()
    categories = ["song", "prayer", "talk", "report", "study", "other"]
    form_rows = [
        {
            "part_type_id": str(template_part.part_type_id),
            "part_name": template_part.part_type.label,
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
                        parts_by_id[part_type_id_value].label
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
                        db.func.lower(PartType.display_name) == normalized_name,
                        db.or_(
                            PartType.church_id.is_(None),
                            PartType.church_id == current_user.church_id,
                        ),
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
                        name=f"{current_user.church_id}:{name}",
                        display_name=name,
                        church_id=current_user.church_id,
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
                    church_id=current_user.church_id,
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


@app.route("/program-types/<int:program_type_id>/delete", methods=["POST"])
@church_admin_required
def delete_program_template(program_type_id):
    program_type = ProgramType.query.filter_by(
        id=program_type_id, church_id=current_user.church_id
    ).first_or_404()
    if Program.query.filter_by(
        church_id=current_user.church_id,
        program_type_id=program_type.id,
    ).first():
        flash("This template is used by existing programs and cannot be deleted.", "error")
    else:
        template_name = program_type.label
        db.session.delete(program_type)
        db.session.commit()
        flash(f"{template_name} template deleted.", "success")
    return redirect(url_for("programs"))


@app.route("/calendar", methods=["GET", "POST"])
@login_required
def calendar():
    today = date.today()

    if request.method == "POST":
        if current_user.role != "church_admin":
            abort(403)
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
            church_id=current_user.church_id,
            year=assignment_month.year,
            month=assignment_month.month,
        ).first()
        if elder_name:
            if assignment:
                assignment.elder_name = elder_name
            else:
                db.session.add(MonthlyElderAssignment(
                    church_id=current_user.church_id,
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
    if current_user.role == "mission_admin":
        district, church_id, status = mission_filter_values()
    else:
        district, church_id, status = "", None, "all"
    month_programs = (
        apply_program_filters(
            Program.query.filter(
            Program.program_date >= month_start,
            Program.program_date < next_month,
            ),
            district, church_id, status,
        )
        .order_by(Program.program_date, Program.program_type_id)
        .all()
    )
    programs_by_date = {}
    for program in month_programs:
        programs_by_date.setdefault(program.program_date.isoformat(), []).append(program)

    selected_programs = (
        apply_program_filters(
            Program.query.filter_by(program_date=selected_date),
            district, church_id, status,
        )
        .order_by(Program.program_type_id)
        .all()
    )
    monthly_elder = None
    if current_user.role == "church_admin":
        monthly_elder = MonthlyElderAssignment.query.filter_by(
            church_id=current_user.church_id,
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
        districts=sorted({church.district for church in Church.query.all()})
        if current_user.role == "mission_admin" else [],
        churches=Church.query.order_by(Church.district, Church.name).all()
        if current_user.role == "mission_admin" else [],
        district_filter=district,
        church_filter=church_id,
        status_filter=status,
    )


@app.route("/programs/new", methods=["GET", "POST"])
@church_admin_required
def new_program():
    types = local_program_types().all()
    if not types:
        flash("This church has no program templates configured.", "error")
        return redirect(url_for("programs"))
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

        program_type = ProgramType.query.filter_by(
            id=type_id, church_id=current_user.church_id
        ).first() if type_id else None

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
                    program_type.id,
                    start_date + timedelta(weeks=week),
                    current_user.church_id,
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
    program = get_visible_program_or_404(program_id)
    return render_template(
        "program_detail.html",
        program=program,
        statuses=PROGRAM_STATUSES,
        unassigned=count_unassigned(program),
    )


def get_visible_program_or_404(program_id):
    query = Program.query.filter_by(id=program_id)
    if current_user.role == "church_admin":
        query = query.filter_by(church_id=current_user.church_id)
    elif current_user.role != "mission_admin":
        abort(403)
    return query.first_or_404()


@app.route("/programs/<int:program_id>/edit", methods=["GET", "POST"])
@church_admin_required
def edit_program(program_id):
    program = get_local_or_404(Program, program_id)
    members = (
        Member.query
        .filter_by(church_id=current_user.church_id, is_active=True)
        .order_by(Member.full_name)
        .all()
    )
    member_options = [{"id": member.id, "label": member.label} for member in members]
    song_options = [
        {"id": song.id, "title": song.title}
        for song in Song.query.filter_by(
            church_id=current_user.church_id
        ).order_by(Song.title, Song.id).all()
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

            song = get_local_or_404(Song, song_id) if song_id else None
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

            member = get_local_or_404(Member, member_id) if member_id else None
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
@church_admin_required
def update_program_status(program_id):
    program = get_local_or_404(Program, program_id)
    status = request.form.get("status", "")

    if status in PROGRAM_STATUSES:
        program.status = status
        db.session.commit()
        flash(f"Status changed to {status}.", "success")
    else:
        flash("Invalid status.", "error")
    return redirect(url_for("program_detail", program_id=program.id))


@app.route("/programs/<int:program_id>/delete", methods=["POST"])
@church_admin_required
def delete_program(program_id):
    program = get_local_or_404(Program, program_id)
    db.session.delete(program)
    db.session.commit()
    flash("Program deleted.", "success")
    return redirect(url_for("programs"))


# =====================================================================
# 9. DATABASE MIGRATIONS AND APP STARTUP
# =====================================================================

def _sqlite_rebuild_scoped_unique_table(table_name, new_table_sql, columns):
    with db.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(new_table_sql)
            column_list = ", ".join(columns)
            connection.exec_driver_sql(
                f"INSERT INTO {table_name}_new ({column_list}) "
                f"SELECT {column_list} FROM {table_name}"
            )
            connection.exec_driver_sql(f"DROP TABLE {table_name}")
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name}_new RENAME TO {table_name}"
            )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _ensure_scoped_unique_constraint(table_name, old_columns, new_constraint, new_columns):
    constraints = inspect(db.engine).get_unique_constraints(table_name)
    if not any(set(item.get("column_names") or []) == set(new_columns) for item in constraints):
        old_constraint = next(
            (
                item for item in constraints
                if set(item.get("column_names") or []) == set(old_columns)
            ),
            None,
        )
        if db.engine.dialect.name == "sqlite":
            if old_constraint:
                if table_name == "programs":
                    columns = [
                        "id", "church_id", "program_type_id", "program_date",
                        "status", "notes", "created_at", "updated_at",
                    ]
                    create_sql = """
                        CREATE TABLE programs_new (
                            id INTEGER NOT NULL PRIMARY KEY,
                            church_id INTEGER NOT NULL REFERENCES churches(id),
                            program_type_id INTEGER NOT NULL REFERENCES program_types(id),
                            program_date DATE NOT NULL,
                            status VARCHAR(20),
                            notes TEXT,
                            created_at DATETIME,
                            updated_at DATETIME,
                            CONSTRAINT uq_program_church_type_date
                                UNIQUE (church_id, program_type_id, program_date)
                        )
                    """
                else:
                    columns = ["id", "church_id", "year", "month", "elder_name"]
                    create_sql = """
                        CREATE TABLE monthly_elder_assignments_new (
                            id INTEGER NOT NULL PRIMARY KEY,
                            church_id INTEGER NOT NULL REFERENCES churches(id),
                            year INTEGER NOT NULL,
                            month INTEGER NOT NULL,
                            elder_name VARCHAR(100) NOT NULL,
                            CONSTRAINT uq_monthly_elder_church_year_month
                                UNIQUE (church_id, year, month)
                        )
                    """
                _sqlite_rebuild_scoped_unique_table(table_name, create_sql, columns)
            else:
                with db.engine.begin() as connection:
                    quoted_columns = ", ".join(
                        db.engine.dialect.identifier_preparer.quote(column)
                        for column in new_columns
                    )
                    connection.exec_driver_sql(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {new_constraint} "
                        f"ON {table_name} ({quoted_columns})"
                    )
        else:
            quote = db.engine.dialect.identifier_preparer.quote
            with db.engine.begin() as connection:
                if old_constraint:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {quote(table_name)} "
                        f"DROP CONSTRAINT {quote(old_constraint['name'])}"
                    )
                constraint_columns = ", ".join(quote(column) for column in new_columns)
                connection.exec_driver_sql(
                    f"ALTER TABLE {quote(table_name)} "
                    f"ADD CONSTRAINT {quote(new_constraint)} "
                    f"UNIQUE ({constraint_columns})"
                )


def migrate_tenant_schema():
    column_additions = {
        "users": {"church_id": "INTEGER REFERENCES churches(id)"},
        "members": {"church_id": "INTEGER REFERENCES churches(id)"},
        "songs": {"church_id": "INTEGER REFERENCES churches(id)"},
        "program_types": {
            "church_id": "INTEGER REFERENCES churches(id)",
            "display_name": "VARCHAR(50)",
        },
        "program_type_parts": {"church_id": "INTEGER REFERENCES churches(id)"},
        "monthly_elder_assignments": {
            "church_id": "INTEGER REFERENCES churches(id)"
        },
        "programs": {"church_id": "INTEGER REFERENCES churches(id)"},
        "program_items": {"song_id": "INTEGER REFERENCES songs(id)"},
        "part_types": {
            "church_id": "INTEGER REFERENCES churches(id)",
            "display_name": "VARCHAR(50)",
        },
    }

    with db.engine.begin() as connection:
        for table_name, additions in column_additions.items():
            columns = {
                column["name"] for column in inspect(db.engine).get_columns(table_name)
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"
                    )

    churches_exist = Church.query.first() is not None
    legacy_data_exists = any(
        db.session.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1")).first()
        for table_name in (
            "users", "members", "songs", "program_types",
            "programs", "monthly_elder_assignments", "part_types",
        )
    )
    if not churches_exist and legacy_data_exists:
        legacy_church = Church(
            name="Legacy Church",
            district="Unassigned District",
            registration_code=secrets.token_urlsafe(24).upper(),
        )
        db.session.add(legacy_church)
        db.session.flush()
        church_id = legacy_church.id
        db.session.commit()
        with db.engine.begin() as connection:
            for table_name in (
                "members", "songs", "programs", "monthly_elder_assignments"
            ):
                connection.execute(
                    text(
                    f"UPDATE {table_name} SET church_id = :church_id "
                    "WHERE church_id IS NULL"
                    ),
                    {"church_id": church_id},
                )
            connection.execute(
                text(
                "UPDATE program_types SET church_id = :church_id, "
                "display_name = name WHERE church_id IS NULL"
                ),
                {"church_id": church_id},
            )
            connection.execute(
                text(
                "UPDATE program_type_parts SET church_id = :church_id "
                "WHERE church_id IS NULL"
                ),
                {"church_id": church_id},
            )
            connection.exec_driver_sql(
                "UPDATE users SET role = 'mission_admin', church_id = NULL "
                "WHERE role = 'admin'"
            )
            connection.execute(
                text(
                "UPDATE users SET role = 'church_admin', church_id = :church_id "
                "WHERE role NOT IN ('mission_admin', 'church_admin') OR role IS NULL"
                ),
                {"church_id": church_id},
            )
            connection.execute(
                text(
                "UPDATE users SET church_id = :church_id "
                "WHERE role = 'church_admin' AND church_id IS NULL"
                ),
                {"church_id": church_id},
            )
            # Keep old church templates attached to their data while freeing the
            # original names for the shared master template catalog.
            connection.execute(
                text(
                "UPDATE program_types SET "
                "name = substr(name, 1, 36) || ' [legacy ' || id || ']' "
                "WHERE church_id = :church_id"
                ),
                {"church_id": church_id},
            )
        db.session.commit()

    legacy_church = Church.query.filter_by(name="Legacy Church").first()
    if legacy_church:
        for part in PartType.query.filter_by(church_id=None).all():
            part.display_name = part.display_name or part.name
            if part.name not in PART_INFO:
                part.church_id = legacy_church.id
        db.session.commit()
        with db.engine.begin() as connection:
            for table_name in (
                "members", "songs", "programs", "monthly_elder_assignments"
            ):
                connection.execute(
                    text(
                    f"UPDATE {table_name} SET church_id = :church_id "
                    "WHERE church_id IS NULL"
                    ),
                    {"church_id": legacy_church.id},
                )
            connection.execute(
                text(
                "UPDATE program_type_parts SET church_id = :church_id "
                "WHERE church_id IS NULL AND program_type_id IN "
                "(SELECT id FROM program_types WHERE church_id = :church_id)"
                ),
                {"church_id": legacy_church.id},
            )

    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email)"
        )
        connection.exec_driver_sql(
            "UPDATE program_types SET display_name = name WHERE display_name IS NULL"
        )
        connection.exec_driver_sql(
            "UPDATE part_types SET display_name = name WHERE display_name IS NULL"
        )

    if db.engine.dialect.name == "postgresql":
        part_name_column = next(
            column for column in inspect(db.engine).get_columns("part_types")
            if column["name"] == "name"
        )
        if getattr(part_name_column["type"], "length", 0) < 100:
            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE part_types ALTER COLUMN name TYPE VARCHAR(100)"
                )

    _ensure_scoped_unique_constraint(
        "programs", ["program_type_id", "program_date"],
        "uq_program_church_type_date",
        ["church_id", "program_type_id", "program_date"],
    )
    _ensure_scoped_unique_constraint(
        "monthly_elder_assignments", ["year", "month"],
        "uq_monthly_elder_church_year_month",
        ["church_id", "year", "month"],
    )


with app.app_context():
    db.create_all()
    migrate_tenant_schema()
    seed_defaults()

if __name__ == "__main__":
    app.run(debug=True)