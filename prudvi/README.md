# Blood Bank Management System - Flask + SQLite

## Features
- Roles: admin, donor, volunteer, hospital
- OTP verification during registration (demo OTP shown in flash message)
- Donor profile with age, gender, last donated date, donation request, and donation history
- Volunteer dashboard with donor availability toggle and GPS update
- Hospital blood request management
- Admin overview
- Real-time style blood inventory and low stock notifications
- Delivery handover updates by volunteer
- SQLite database

## Demo accounts
- admin@bloodflow.com / password123
- donor1@bloodflow.com / password123
- volunteer1@bloodflow.com / password123
- hospital1@bloodflow.com / password123

## Run
```bash
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`

## Notes
- OTP is implemented for demo/testing. Integrate SMS or email provider for production.
- GPS uses browser geolocation API and requires location permission.
