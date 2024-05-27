import calendar
import random
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from flask import render_template, flash, redirect, url_for, Blueprint, request, jsonify, Response, send_file, session
from flask_login import current_user, login_user, logout_user, login_required
from werkzeug.utils import secure_filename
from src.config import UPLOAD_FOLDER, EMAIL_KEY
from src.forms import LoginForm, MeterForm, UploadForm, UserForm, EditAccountForm, \
    UserNotesForm, UserOverviewForm, MessageForm, AssignMeterToSuperuserForm, AssignMeterToUserForm, EditUserForm, \
    MONTHS_PL
from src.models import User, db, Meter, MeterReading, get_all_users, Message, Address, MeterEditHistory, UserReportMonth
import os
from src.utils import process_csv_water, process_csv_heat, admin_required, is_valid_link, process_csv_events, \
    superuser_required, create_report_data, generate_random_password, remove_duplicate_readings

main_routes = Blueprint('main_routes', __name__)
admin_routes = Blueprint('admin_routes', __name__)
superuser_routes = Blueprint('superuser_routes', __name__)
user_routes = Blueprint('user_routes', __name__)


@main_routes.route('/')
def welcome():
    if current_user.is_authenticated == True:
        return redirect(url_for("main_routes.home"))
    else:
        return redirect(url_for("main_routes.login"))


@main_routes.route('/home')
def home():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("admin_routes.admin_panel"))
        if current_user.is_superuser:
            return redirect(url_for("main_routes.superuser_panel"))
        user = current_user
        assigned_meters = user.meters
        return render_template('home.html', assigned_meters=assigned_meters)
    return redirect(url_for('main_routes.login'))


@main_routes.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main_routes.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()

        if user is None or not user.check_password(form.password.data):
            flash('Nieprawidłowy email lub hasło.', 'danger')
            return redirect(url_for('main_routes.login'))
        if not user.is_active:
            flash('Konto zostało dezaktywowane, skontaktuj się z administracją.', 'warning')
            return redirect(url_for('main_routes.login'))

        login_user(user)
        flash('Zalogowano pomyślnie.', 'success')
        return redirect(url_for('main_routes.home'))

    return render_template('login.html', form=form)


@main_routes.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('main_routes.home'))


@admin_routes.route('/upload_csv', methods=['GET', 'POST'])
@admin_required
def upload_csv():
    form = UploadForm()
    if form.validate_on_submit():
        device_type = form.device_type.data
        file = form.file.data
        file_path = os.path.join(UPLOAD_FOLDER, secure_filename(file.filename))
        file.save(file_path)

        if device_type == 'water':
            result_message = process_csv_water(file_path)
            if "Problem" in result_message:
                flash(result_message, 'danger')
                return redirect(url_for('main_routes.home'))
        elif device_type == 'heat':
            result_message = process_csv_heat(file_path)
            if "Problem" in result_message:
                flash(result_message, 'danger')
                return redirect(url_for('admin_routes.upload_csv'))
        elif device_type == 'events_water':
            # Nowy kod dla plików zdarzeń
            process_csv_events(file_path, 'water')
        elif device_type == 'events_heat':
            # Nowy kod dla plików zdarzeń
            process_csv_events(file_path, 'heat')

        flash('Plik CSV przesłany pomyślnie.')
        if os.path.exists(file_path):
            os.remove(file_path)
        return redirect(url_for('main_routes.home'))

    return render_template('upload_csv.html', form=form)


@user_routes.route('/user_meters')
@login_required
def user_meters():
    user_meters = Meter.query.filter_by(user_id=current_user.id).all()
    return render_template('user_meters.html', user_meters=user_meters)


@main_routes.route('/meter_details/<int:meter_id>', methods=['GET', 'POST'])
@login_required
def meter_details(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    user = meter.user
    events = meter.events
    # Sprawdzenie, czy bieżący użytkownik to właściciel licznika, administrator, lub superużytkownik z dostępem do tego licznika
    if current_user != meter.user and not current_user.is_admin and not (
            current_user.is_superuser and meter.superuser_id == current_user.id):
        flash('Brak uprawnień do wyświetlenia tych szczegółów.', 'danger')
        return redirect(url_for('main_routes.home'))

    readings = MeterReading.query.filter_by(meter_id=meter.id).all()
    readings_list = [{"date": reading.date, "reading": reading.reading} for reading in readings]
    if user:
        user_months = {user.id: [month.month for month in user.report_months]}
    if current_user.is_superuser:
        for assigned_user in current_user.assigned_users:
            user_months[assigned_user.id] = [month.month for month in assigned_user.report_months]

    if not current_user.is_admin:
        user_accessible_months = user_months.get(meter.user.id, [])
        readings_list = [reading for reading in readings_list if reading['date'].month in user_accessible_months]
    return render_template('meter_details.html', meter=meter, readings=readings_list, user=user, events=events)


@main_routes.route('/delete_meter/<int:meter_id>', methods=['POST'])
@admin_required
def delete_meter(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    db.session.delete(meter)
    db.session.commit()
    flash('Licznik został usunięty.', 'success')
    return redirect(url_for('admin_routes.admin_panel'))


@main_routes.route('/clear_readings/<int:meter_id>', methods=['POST'])
@admin_required
def clear_readings(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    meter.readings.delete()
    db.session.commit()
    flash('Odczyty licznika zostały wyczyszczone.', 'success')
    return redirect(url_for('main_routes.meter_details', meter_id=meter_id))


@main_routes.route('/update_meter_name/<int:meter_id>', methods=['POST'])
@login_required
def update_meter_name(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    if current_user == meter.user:
        new_name = request.form.get('new_name')
        if new_name:
            meter.name = new_name
            db.session.commit()
            flash('Pomyślnie zmieniono nazwę.', 'success')
        else:
            flash('Nazwa nie może być pusta.', 'danger')
    else:
        flash('Nie masz uprawnień do zmiany nazwy licznika.', 'danger')
    return redirect(url_for('main_routes.meter_details', meter_id=meter.id))


@main_routes.route('/update_meter_address/<int:meter_id>', methods=['POST'])
@login_required
def update_meter_address(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    if request.method == 'POST':
        city = request.form.get('city')
        street = request.form.get('street')
        building_number = request.form.get('building_number')
        apartment_number = request.form.get('apartment_number')
        postal_code = request.form.get('postal_code')
        edit_details = ""

        if meter.address:
            address = meter.address
            # Sprawdź, które pola zostały zmienione i zapisz szczegóły
            if address.city != city:
                edit_details += f"City changed from {address.city} to {city}. "
            if address.street != street:
                edit_details += f"Street changed from {address.street} to {street}. "
            if address.building_number != building_number:
                edit_details += f"Building number changed from {address.building_number} to {building_number}. "
            if address.apartment_number != apartment_number:
                edit_details += f"Apartment number changed from {address.apartment_number} to {apartment_number}. "
            if address.postal_code != postal_code:
                edit_details += f"Postal code changed from {address.postal_code} to {postal_code}. "

            address.city = city
            address.street = street
            address.building_number = building_number
            address.apartment_number = apartment_number
            address.postal_code = postal_code
        else:
            address = Address(city=city, street=street, building_number=building_number,
                              apartment_number=apartment_number, postal_code=postal_code)
            meter.address = address
            edit_details = f"Address added.{address.city}, {address.street}, {address.building_number}, {address.apartment}"
            db.session.add(address)

        # Dodaj wpis do historii edycji
        if edit_details:
            new_history_entry = MeterEditHistory(meter_id=meter_id, user_id=current_user.id,
                                                 edit_type='Change Address', edit_details=edit_details)
            db.session.add(new_history_entry)

        db.session.commit()
    return redirect(url_for('main_routes.meter_details', meter_id=meter.id))


@admin_routes.route('/admin_panel', methods=['GET'])
@admin_required  # Dodaj dekorator, aby wymagać uprawnień administratora
def admin_panel():
    user_form = UserForm()

    if user_form.validate_on_submit():
        user = User(email=user_form.email.data)
        user.set_password(user_form.password.data)
        if user_form.is_admin.data:
            user.is_admin = True
        if user_form.is_superuser.data:
            user.is_superuser = True
        db.session.add(user)
        db.session.commit()
        flash('Użytkownik został dodany.')
        return redirect(url_for('admin_routes.admin_panel'))

    users = get_all_users()
    meters = Meter.query.all()
    return render_template('admin_panel.html', users=users, meters=meters, user_form=user_form)


@main_routes.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    user_form = UserForm()

    if user_form.validate_on_submit():
        email = user_form.email.data
        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash('Użytkownik o podanym adresie email już istnieje.', 'warning')
            return redirect(url_for('admin_routes.admin_panel'))
        user = User(email=user_form.email.data)
        user.set_password(user_form.password.data)
        if user_form.is_admin.data:
            user.is_admin = True
        if user_form.is_superuser.data:
            user.is_superuser = True
        db.session.add(user)
        db.session.commit()
        flash('Dodawanie użytkownika przebiegło pomyślnie.')
        return redirect(url_for('admin_routes.admin_panel'))

    users = get_all_users()
    meters = Meter.query.all()
    return render_template('admin_panel.html', users=users, meters=meters, user_form=user_form)


@main_routes.route('/user_overview/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def user_overview(user_id):
    user = User.query.get(user_id)
    is_superuser = user.is_superuser
    is_admin = user.is_admin
    available_meters = Meter.query.filter_by(user_id=None).all()
    unassigned_meters = Meter.query.filter_by(user=None).all()
    assigned_users = []
    unassigned_users = []
    assigned_meters = []
    if is_superuser:
        assigned_users = User.query.filter(User.superuser_id == user_id).all()
        unassigned_users = User.query.filter(User.superuser_id == None, User.is_admin == False,
                                             User.is_superuser == False).all()
        assigned_meters = Meter.query.filter_by(superuser_id=user_id).all()

    user_form = UserForm()
    user_notes_form = UserOverviewForm()  # Dodaj nowy formularz dla notatek

    users = get_all_users()
    meters = Meter.query.all()

    edit_user_form = EditUserForm(email=user.email)
    edit_user_form.report_months.choices = [(str(i), MONTHS_PL[i]) for i in range(1, 13)]
    selected_months = [str(rm.month) for rm in user.report_months.all()]
    edit_user_form.report_months.data = selected_months

    if request.method == 'POST':
        print(request.form)
        if edit_user_form.validate_on_submit():
            user.email = edit_user_form.email.data
            if edit_user_form.password.data:  # Sprawdź, czy hasło zostało podane
                user.set_password(edit_user_form.password.data)

            UserReportMonth.query.filter_by(user_id=user.id).delete()
            for month in request.form.getlist('report_months[]'):
                new_month = UserReportMonth(user_id=user.id, month=int(month))
                print(new_month)
                db.session.add(new_month)

            db.session.commit()
            flash('Dane użytkownika zostały zaktualizowane.', 'success')
            return redirect(url_for('main_routes.user_overview', user_id=user.id))

        if 'meter_id' in request.form and request.form['meter_id']:
            meter_id = int(request.form.get('meter_id'))
            meter = Meter.query.get(meter_id)
            meter.user = user
            db.session.commit()
            flash('Licznik został pomyślnie przypisany do użytkownika.')

        if user_notes_form.validate_on_submit():
            user.notes = user_notes_form.notes.data
            db.session.commit()
            flash('Notatki zostały zaktualizowane.')

    return render_template(
        'user_overview.html',
        user=user,
        available_meters=available_meters,
        unassigned_meters=unassigned_meters,
        user_form=user_form,
        user_notes_form=user_notes_form,  # Przekaż nowy formularz do szablonu
        users=users,
        meters=meters,
        is_superuser=is_superuser,
        is_admin=is_admin,
        assigned_users=assigned_users,
        unassigned_users=unassigned_users,
        assigned_meters=assigned_meters,
        edit_user_form=edit_user_form,
    )


@main_routes.route('/remove_meter/<int:meter_id>')
@superuser_required
def remove_meter(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    if meter.user:
        user_id = meter.user.id
        # Dodatkowa weryfikacja dla superusera
        if meter.user.superuser_id != current_user.id and not current_user.is_admin:
            flash('Brak uprawnień do odłączenia tego licznika.', 'danger')
            return redirect(url_for('main_routes.superuser_user_overview', user_id=user_id))
    else:
        user_id = None
    meter.user = None
    meter.superuser_id = None

    db.session.commit()
    flash('Licznik został odłączony od użytkownika.', 'success')
    if current_user.is_superuser:
        return redirect(url_for('main_routes.superuser_user_overview', user_id=user_id))
    else:
        return redirect(url_for('main_routes.user_overview', user_id=user_id))


@main_routes.route('/add_meter', methods=['GET', 'POST'])
@admin_required
def add_meter():
    form = MeterForm()
    if form.validate_on_submit():
        meter = Meter(radio_number=form.radio_number.data, type=form.type.data, user_id=form.user_id.data)
        db.session.add(meter)
        db.session.commit()
        flash('Licznik został dodany.')
        return redirect(url_for('admin_routes.admin_panel'))
    return render_template('add_meter.html', form=form)


@main_routes.route('/edit_account', methods=['GET', 'POST'])
@login_required
def edit_account():
    form = EditAccountForm()
    user_email = current_user.email
    if form.validate_on_submit():
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Hasło zostało zmienione.')
        return redirect(url_for('main_routes.home'))

    return render_template('edit_account.html', form=form, user_email=user_email)


@main_routes.route('/user/<int:user_id>/assign_meter/<int:meter_id>', methods=['GET', 'POST'])
@superuser_required
def assign_meter(user_id, meter_id):
    user = User.query.get(user_id)
    meter = Meter.query.get(meter_id)

    if user and meter:
        if current_user.is_superuser:
            # Dodatkowa weryfikacja dla superusera
            if meter.superuser_id == current_user.id and user.superuser_id == current_user.id:
                meter.user_id = user.id
            else:
                flash('Brak uprawnień do przypisania tego licznika.', 'danger')
                return redirect(url_for('main_routes.superuser_user_overview', user_id=user_id))
        else:
            meter.user_id = user.id
            if user.superuser_id:
                meter.superuser_id = user.superuser_id
        db.session.commit()
        flash('Licznik został pomyślnie przypisany.', 'success')
    else:
        flash('Wystąpił błąd przy przypisaniu licznika.', 'danger')

    if current_user.is_superuser:
        return redirect(url_for('main_routes.superuser_user_overview', user_id=user_id))
    else:
        return redirect(url_for('main_routes.user_overview', user_id=user_id))


@main_routes.route('/delete_meters', methods=['POST'])
@admin_required
def delete_meters():
    try:
        MeterReading.query.delete()
        Meter.query.delete()
        Address.query.delete()
        db.session.commit()
        flash('Wszystkie mierniki i odczyty zostały usunięte.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Wystąpił błąd podczas usuwania mierników i odczytów.', 'danger')
        print(f"Error deleting meters: {e}")
    return redirect(url_for('admin_routes.admin_panel'))


@main_routes.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get(user_id)
    admin_password = request.form.get('admin_password')

    # Sprawdź, czy wprowadzone hasło administratora jest prawidłowe
    if not current_user.check_password(admin_password):
        flash('Nieprawidłowe hasło administratora.', 'danger')
        return redirect(url_for('main_routes.user_overview', user_id=user_id))

    # Usuń użytkownika
    db.session.delete(user)
    db.session.commit()

    flash('Użytkownik został usunięty.', 'success')
    return redirect(url_for('admin_routes.admin_panel'))


@main_routes.route('/deactivate_user/<int:user_id>', methods=['POST'])
@admin_required
def deactivate_user(user_id):
    user = User.query.get(user_id)
    if user.is_active:
        user.is_active = False
        flash('Konto użytkownika zostało dezaktywowane.', 'warning')
    else:
        user.is_active = True
        flash('Konto użytkownika zostało aktywowane.', 'success')
    db.session.commit()

    return redirect(url_for('main_routes.user_overview', user_id=user_id))


@main_routes.route('/update_user_notes/<int:user_id>', methods=['POST'])
@admin_required
def update_user_notes(user_id):
    user = User.query.get_or_404(user_id)
    form = UserNotesForm()
    if form.validate_on_submit():
        user.notes = form.notes.data
        db.session.commit()
        flash('Notatki zostały zaktualizowane.', 'success')

    return redirect(url_for('main_routes.user_overview', form=form, user_id=user.id))


@main_routes.route('/messages', methods=['GET', 'POST'])
@login_required
def messages():
    form = MessageForm()

    if request.form.get('mark_all_as_read'):
        unread_messages = Message.query.filter_by(recipient_id=current_user.id, read=False).all()
        for message in unread_messages:
            message.read = True
        current_user.unread_messages = 0
        db.session.commit()
        flash('Wszystkie wiadomości zostały oznaczone jako przeczytane.', 'success')

    if current_user.is_admin:
        form.recipient.choices = [(user.id, user.email) for user in User.query.filter(User.is_admin == False)]
    else:
        form.recipient.choices = [(current_user.id, current_user.email)]

    if form.validate_on_submit():
        recipient_ids = request.form.getlist('recipient')
        subject = form.subject.data
        content = form.content.data

        for recipient_id in recipient_ids:
            recipient = User.query.get(int(recipient_id))
            if recipient:
                message = Message(sender_id=current_user.id, recipient_id=recipient.id, subject=subject,
                                  content=content)
                db.session.add(message)
                recipient.unread_messages += 1
            else:
                flash(f'Odbiorca o ID {recipient_id} nie istnieje.', 'danger')

        db.session.commit()
        flash('Wiadomość została wysłana!', 'success')
        return redirect(url_for('main_routes.messages'))

    return render_template('messages.html', form=form)


@main_routes.route('/message/<int:message_id>')
@login_required
def message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.recipient_id == current_user.id and not message.read:
        message.read = True
        current_user.unread_messages -= 1
        db.session.commit()
    return render_template('message.html', message=message)


@main_routes.route('/delete_message/<int:message_id>')
@login_required
def delete_message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.recipient_id == current_user.id and not message.read:
        current_user.unread_messages -= 1
    db.session.delete(message)
    db.session.commit()
    flash('Wiadomość usunięta!', 'success')
    return redirect(url_for('main_routes.messages'))


@main_routes.route('/assign_meters_to_user/<int:user_id>', methods=['POST'])
@admin_required
def assign_meters_to_user(user_id):
    user = User.query.get_or_404(user_id)

    meter_list = request.form.get('meter_list')
    meter_numbers = [num.strip() for num in meter_list.split(',')]

    successfully_assigned = []
    not_assigned = []

    for meter_number in meter_numbers:
        meter = Meter.query.filter_by(radio_number=meter_number).first()
        if meter and meter.user_id is None:
            meter.user_id = user.id
            db.session.commit()
            successfully_assigned.append(meter_number)
        else:
            not_assigned.append((meter_number, "Przypisanie niemożliwe"))

    flash(not_assigned, 'warning')
    return redirect(url_for('main_routes.user_overview', successfully_assigned=successfully_assigned,
                            not_assigned=not_assigned, user=user, user_id=user.id))


@main_routes.route('/summary', methods=['GET', 'POST'])
def summary():
    filtered_readings = []

    if request.method == 'POST':
        address = request.form.get('address')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')

        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        filtered_readings = MeterReading.query.join(Meter).filter(
            Meter.address == address,
            MeterReading.date >= start_date,
            MeterReading.date <= end_date
        ).all()

    return render_template('summary.html', readings=filtered_readings)


@main_routes.route('/admin/assign_meter_to_superuser', methods=['GET', 'POST'])
@admin_required
def assign_meter_to_superuser():
    if not current_user.is_admin:
        flash('Brak uprawnień', 'danger')
        return redirect(url_for('main_routes.home'))

    form = AssignMeterToSuperuserForm()
    if form.validate_on_submit():
        meter = Meter.query.get(form.meter_id.data)
        superuser = User.query.get(form.superuser_id.data)
        if meter and superuser:
            meter.superuser_id = superuser.id
            db.session.commit()
            flash('Licznik przypisany do superużytkownika', 'success')
        else:
            flash('Błąd podczas przypisywania licznika', 'danger')
        return redirect(url_for('main_routes.admin_dashboard'))

    return render_template('admin/assign_meter_to_superuser.html', form=form)


@main_routes.route('/superuser/assign_meter', methods=['GET', 'POST'])
@login_required
def superuser_assign_meter():
    if not current_user.is_superuser:
        flash('Brak uprawnień', 'danger')
        return redirect(url_for('main_routes.home'))

    form = AssignMeterToUserForm(superuser_id=current_user.id)
    if form.validate_on_submit():
        user = User.query.get(form.user_id.data)
        meter = Meter.query.get(form.meter_id.data)
        if user and meter:
            # Logika przypisywania licznika do użytkownika
            flash('Licznik przypisany do użytkownika', 'success')
        else:
            flash('Błąd podczas przypisywania licznika', 'danger')
        return redirect(url_for('main_routes.superuser_dashboard'))

    return redirect(url_for('main_routes.superuser_dashboard', form=form, user_id=form.user_id.data))


@main_routes.route('/superuser_user_overview/<int:user_id>', methods=['GET', 'POST'])
@superuser_required
def superuser_user_overview(user_id):
    user = User.query.get(user_id)
    users = User.query.filter_by(superuser_id=current_user.id).all()
    if user.superuser_id != current_user.id:
        flash('Brak dostępu do tego użytkownika.', 'danger')
        return redirect(url_for('main_routes.home'))

    # Pobieranie liczników przypisanych do superużytkownika, ale nieprzypisanych do żadnego użytkownika
    available_meters = Meter.query.filter_by(superuser_id=current_user.id, user_id=None).all()

    user_form = UserForm(obj=user)
    user_notes_form = UserOverviewForm(obj=user)

    if 'meter_id' in request.form and request.form['meter_id']:
        meter_id = int(request.form.get('meter_id'))
        meter = Meter.query.get(meter_id)
        if meter.superuser_id == current_user.id:
            meter.user_id = user.id
            db.session.commit()
            flash('Licznik został pomyślnie przypisany do użytkownika.')
        else:
            flash('Nie masz uprawnień do przypisania tego licznika.', 'danger')

    if user_notes_form.validate_on_submit():
        user.notes = user_notes_form.notes.data
        db.session.commit()
        flash('Notatki zostały zaktualizowane.')

    return render_template(
        'superuser_user_overview.html',
        user=user,
        available_meters=available_meters,
        user_form=user_form,
        user_notes_form=user_notes_form,
        users=users,
    )


@main_routes.route('/superuser_panel', methods=['GET', 'POST'])
@login_required
def superuser_panel():
    user_form = UserForm()

    if user_form.validate_on_submit():
        user = User(email=user_form.email.data)
        user.set_password(user_form.password.data)
        user.superuser_id = current_user.id  # Przypisanie użytkownika do superużytkownika
        db.session.add(user)
        db.session.commit()
        flash('Użytkownik został dodany.')
        return redirect(url_for('main_routes.superuser_panel'))

    users = User.query.filter_by(superuser_id=current_user.id).all()
    meters = Meter.query.filter_by(superuser_id=current_user.id).all()
    return render_template('superuser_panel.html', users=users, meters=meters, user_form=user_form)


@main_routes.route('/assign_user_to_superuser/<int:superuser_id>/<int:user_id>', methods=['POST'])
@admin_required
def assign_user_to_superuser(superuser_id, user_id):
    superuser = User.query.get_or_404(superuser_id)
    user_to_assign = User.query.get_or_404(user_id)

    if not superuser.is_superuser:
        flash('Wybrany użytkownik nie jest superuserem.', 'error')
        return redirect(url_for('main_routes.user_overview', user_id=superuser_id))

    user_to_assign.superuser_id = superuser_id

    for meter in user_to_assign.meters:
        meter.superuser_id = superuser_id

    db.session.commit()
    flash('Użytkownik został przypisany do superusera.', 'success')

    return redirect(url_for('main_routes.user_overview', user_id=superuser_id))


@main_routes.route('/remove_assigned_user/<int:user_id>')
@superuser_required
def remove_assigned_user(user_id):
    assigned_user = User.query.get_or_404(user_id)

    # Sprawdzenie, czy obecny użytkownik ma uprawnienia do usunięcia przypisania
    if current_user.is_admin:
        assigned_user.superuser_id = None
        db.session.commit()
        flash('Przypisanie użytkownika zostało usunięte.', 'success')
        return redirect(url_for('main_routes.user_overview', user_id=current_user.id))
    if current_user.is_superuser and assigned_user.superuser_id == current_user.id:
        assigned_user.superuser_id = None
        db.session.commit()
        flash('Przypisanie użytkownika zostało usunięte.', 'success')
    else:
        flash('Brak uprawnień do usunięcia przypisania tego użytkownika.', 'danger')

    return redirect(url_for('main_routes.user_overview', user_id=current_user.id))


@main_routes.route('/generate_report', methods=['GET', 'POST'])
@superuser_required
def generate_report():
    if request.method == 'POST':
        selected_meters = request.form.getlist('selected_meters')
        report_period = int(request.form.get('report_period'))

        if current_user.is_admin:
            users = User.query.all()
            # Administrator ma dostęp do wszystkich miesięcy
            user_months = {user.id: list(range(1, 13)) for user in users}
        else:
            users = User.query.filter_by(superuser_id=current_user.id).all()
            user_months = {user.id: [ur.month for ur in user.report_months] for user in users}

        report_data = create_report_data(selected_meters, user_months, report_period)
        session['report_data'] = report_data  # Zapisz dane do sesji
        session['report_period'] = report_period
        return redirect(url_for('main_routes.display_report'))

    if current_user.is_admin:
        users = User.query.all()
    else:
        users = User.query.filter_by(superuser_id=current_user.id).all()
    return render_template('generate_report.html', users=users)


@main_routes.route('/display_report')
@superuser_required
def display_report():
    report_data = session.get('report_data', [])
    report_period = session.get('report_period', 0)
    end_date = datetime.now().replace(day=1) - relativedelta(days=1)
    report_end_date = datetime.now()
    report_start_date = report_end_date - relativedelta(months=report_period)
    unique_emails = set(data['user_email'] for data in report_data)

    english_to_polish_months = {
        'January': 'Styczeń',
        'February': 'Luty',
        'March': 'Marzec',
        'April': 'Kwiecień',
        'May': 'Maj',
        'June': 'Czerwiec',
        'July': 'Lipiec',
        'August': 'Sierpień',
        'September': 'Wrzesień',
        'October': 'Październik',
        'November': 'Listopad',
        'December': 'Grudzień'
    }
    for data_entry in report_data:
        for month in range(report_period):
            month_name = (end_date - relativedelta(months=month)).strftime('%B %Y')
            month_name_split = month_name.split()
            polish_month_name = english_to_polish_months.get(month_name_split[0], month_name_split[0]) + ' ' + \
                                month_name_split[1]
            if month_name in data_entry:
                data_entry[polish_month_name] = data_entry.pop(month_name)

    translated_month_names = [(end_date - relativedelta(months=month)).strftime('%B %Y') for month in
                              range(report_period)]
    translated_month_names = [english_to_polish_months.get(month.split()[0], month.split()[0]) + ' ' + month.split()[1]
                              for month in translated_month_names]

    return render_template('display_report.html', report_data=report_data,
                           translated_month_names=translated_month_names, report_period=report_period,
                           end_date=end_date, relativedelta=relativedelta,
                           report_start_date=report_start_date.strftime('%Y-%m-%d'),
                           report_end_date=report_end_date.strftime('%Y-%m-%d'), unique_emails=unique_emails)


@main_routes.route('/add_multiple_users', methods=['POST'])
@admin_required
def add_multiple_users():
    emails = request.form.get('emails').split()
    user_data = []

    for email in emails:
        password = generate_random_password()
        email = email.replace(',', '')
        email = email.replace(';', '')
        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        user_data.append({'email': email, 'password': password})

    db.session.commit()
    return render_template('user_summary.html', users=user_data)



@main_routes.route('/edit_meter/<int:meter_id>', methods=['GET', 'POST'])
@login_required
def edit_meter(meter_id):
    if not current_user.is_admin:
        flash('Brak uprawnień do edycji licznika.', 'danger')
        return redirect(url_for('main_routes.home'))

    meter = Meter.query.get_or_404(meter_id)
    readings = MeterReading.query.filter_by(meter_id=meter.id).order_by(MeterReading.date).all()

    if request.method == 'POST':
        new_radio_number = request.form.get('new_radio_number')
        reading_ids_to_delete = request.form.getlist('reading_ids')
        delete_duplicates = request.form.get('delete_duplicates')

        # Aktualizacja numeru radiowego
        if new_radio_number and new_radio_number != meter.radio_number:
            old_radio_number = meter.radio_number
            meter.radio_number = new_radio_number
            # Dodaj wpis do historii edycji
            new_history_entry = MeterEditHistory(meter_id=meter_id, user_id=current_user.id,
                                                 edit_type='Change Radio Number',
                                                 edit_details=f'Changed from {old_radio_number} to {new_radio_number}')
            db.session.add(new_history_entry)

        # Usuwanie wybranych odczytów
        for reading_id in reading_ids_to_delete:
            reading = MeterReading.query.get(int(reading_id))
            if reading:
                db.session.delete(reading)
                # Dodaj wpis do historii edycji
                new_history_entry = MeterEditHistory(meter_id=meter_id, user_id=current_user.id,
                                                     edit_type='Delete Reading',
                                                     edit_details=f'Deleted reading of {reading.date}')
                db.session.add(new_history_entry)

        # Edycja wartości odczytu
        for reading in readings:
            new_reading_value = request.form.get(f'reading_value_{reading.id}')
            if new_reading_value and new_reading_value != str(reading.reading):
                old_reading_value = reading.reading
                reading.reading = float(new_reading_value)
                # Dodaj wpis do historii edycji
                new_history_entry = MeterEditHistory(meter_id=meter_id, user_id=current_user.id,
                                                     edit_type='Edit Reading',
                                                     edit_details=f'Changed reading of {reading.date} from {old_reading_value} to {new_reading_value}')
                db.session.add(new_history_entry)

        # Usuwanie duplikatów
        if delete_duplicates:
            unique_dates = set()
            for reading in readings:
                if reading.date in unique_dates:
                    db.session.delete(reading)
                    # Dodaj wpis do historii edycji
                    new_history_entry = MeterEditHistory(meter_id=meter_id, user_id=current_user.id,
                                                         edit_type='Delete Duplicate',
                                                         edit_details=f'Deleted duplicate reading of {reading.date}')
                    db.session.add(new_history_entry)
                else:
                    unique_dates.add(reading.date)

        db.session.commit()
        flash('Zmiany zostały zapisane.', 'success')
        return redirect(url_for('main_routes.meter_details', meter_id=meter.id))

    return render_template('edit_meter.html', meter=meter, readings=readings)


@main_routes.route('/meter_history/<int:meter_id>')
@login_required
def meter_history(meter_id):
    meter = Meter.query.get_or_404(meter_id)
    if not (current_user.is_admin or current_user.id == meter.user_id or (
            current_user.is_superuser and current_user.id == meter.superuser_id)):
        flash('Brak uprawnień do wyświetlenia historii tego licznika.', 'danger')
        return redirect(url_for('main_routes.home'))

    history = MeterEditHistory.query.filter_by(meter_id=meter_id).order_by(MeterEditHistory.timestamp.desc()).all()
    return render_template('meter_history.html', history=history, meter=meter)


@admin_routes.route('/admin/delete_selected_meters', methods=['POST'])
@admin_required
def delete_selected_meters():
    selected_ids = request.form.getlist('selected_meters')

    if not selected_ids:
        flash('Nie zaznaczono żadnych liczników do usunięcia.', 'warning')
        return redirect(
            url_for('admin_routes.admin_panel'))  # Zakładając, że 'admin_panel' to nazwa funkcji widoku panelu admina

    try:
        # Usuń powiązane rekordy z meter_edit_history
        for meter_id in selected_ids:
            MeterEditHistory.query.filter_by(meter_id=meter_id).delete()
            # Usuń licznik
            Meter.query.filter_by(id=meter_id).delete()

        db.session.commit()
        flash('Wybrane liczniki zostały usunięte.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Wystąpił błąd podczas usuwania liczników.', 'danger')
        main_routes.logger.error(f'Error deleting meters: {e}')

    return redirect(url_for('admin_routes.admin_panel'))


@admin_routes.route('/admin/remove-duplicates', methods=['POST'])
@admin_required
def remove_duplicates():
    total_removed_duplicates = remove_duplicate_readings()
    flash(f'Usunięto {total_removed_duplicates} duplikatów odczytów.', 'success')
    return redirect(url_for('admin_routes.admin_panel'))

# @admin_routes.route('/emitel-readings', methods=['POST'])
# @admin_required  # Upewnij się, że ta trasa jest dostępna tylko dla administratorów
# def emitel_readings():
#     print(request.form)
#     days_back = request.form.get('days_back', type=int)
#     end_date = datetime.now()
#     start_date = end_date - timedelta(days=days_back)
#
#     data = fetch_data_from_db(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
#     # Oblicz liczbę unikalnych liczników, które odpowiedziały
#     unique_meters_count = len(data['DeviceEui'].unique())
#     print(unique_meters_count)
#
#     return jsonify({'message': f'Liczba liczników, które odpowiedziały w ciągu ostatnich {days_back} dni: {unique_meters_count}'})
#
#
# @admin_routes.route('/send-meters-count', methods=['POST'])
# @admin_required  # Upewnij się, że ta trasa jest dostępna tylko dla administratorów
# def send_meters_count():
#     try:
#         success = check_and_email_meters()  # Wywołaj funkcję wysyłającą e-mail
#         if success:
#             flash('E-mail z ilością liczników został wysłany.', 'success')
#         else:
#             flash('E-mail nie został wysłany.', 'warning')
#     except Exception as e:
#         flash(f'Wystąpił błąd: {str(e)}', 'danger')
#
#     return redirect(url_for('admin_routes.admin_panel'))  # Przekieruj z powrotem do panelu admina
