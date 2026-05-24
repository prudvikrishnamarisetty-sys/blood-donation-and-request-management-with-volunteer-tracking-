import os
import sqlite3
import random
import uuid
from datetime import datetime, date, timedelta
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bloodbank.db')

BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
GENDERS = ['Male', 'Female', 'Other']

LOCATIONS = [
    ('Vijayawada, Andhra Pradesh', 16.5062, 80.6480),
    ('Guntur, Andhra Pradesh', 16.3067, 80.4365),
    ('Hyderabad, Telangana', 17.3850, 78.4867),
    ('Anakapalli, Andhra Pradesh', 17.6974, 83.0069),
]

INDIAN_NAMES_M = ['Aarav', 'Vivaan', 'Aditya', 'Vihaan', 'Arjun', 'Sai', 'Reyansh', 'Ayaan', 'Krishna', 'Ishaan', 'Shaurya', 'Atharv', 'Rudra', 'Kabir', 'Dhruv', 'Yash', 'Rishabh', 'Rahul', 'Nikhil', 'Dev']
INDIAN_NAMES_F = ['Aadhya', 'Diya', 'Kashvi', 'Saanvi', 'Ananya', 'Pari', 'Prisha', 'Riya', 'Snigdha', 'Aarohi', 'Nandini', 'Shruti', 'Anjali', 'Neha', 'Priya', 'Kavya', 'Rachana', 'Megha', 'Tanya', 'Akshita']
SURNAMES = ['Sharma', 'Verma', 'Singh', 'Reddy', 'Rao', 'Kumar', 'Patel', 'Yadav', 'Naik', 'Desai', 'Joshi', 'Menon', 'Pillai', 'Iyer', 'Gupta', 'Sethi', 'Bhat', 'Das', 'Sen', 'Sinha']

def get_random_name(gender):
    if gender == 'Male':
        first = random.choice(INDIAN_NAMES_M)
    else:
        first = random.choice(INDIAN_NAMES_F)
    return f"{first} {random.choice(SURNAMES)}"

def seed_data():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    
    password_hash = generate_password_hash('password123')
    
    # 1. Register 50 donors
    print("Generating 50 Donors...")
    for i in range(50):
        gender = random.choice(['Male', 'Female'])
        name = get_random_name(gender)
        email = f'newdonor{i+1}@bloodflow.com'
        phone = f'98{random.randint(10000000, 99999999)}'
        bg = random.choice(BLOOD_GROUPS)
        age = random.randint(18, 60)
        loc = random.choice(LOCATIONS)
        lat = loc[1] + random.uniform(-0.05, 0.05)
        lng = loc[2] + random.uniform(-0.05, 0.05)
        
        c.execute('''INSERT INTO users (full_name,email,phone,password_hash,role,blood_group,gender,age,address,latitude,longitude,is_available,is_verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,1)''',
            (name,email,phone,password_hash,'donor',bg,gender,age,loc[0],lat,lng))
            
    # 2. Register 10 volunteers
    print("Generating 10 Volunteers...")
    for i in range(10):
        gender = random.choice(['Male', 'Female'])
        name = get_random_name(gender)
        email = f'newvol{i+1}@bloodflow.com'
        phone = f'97{random.randint(10000000, 99999999)}'
        age = random.randint(20, 45)
        loc = random.choice(LOCATIONS)
        lat = loc[1] + random.uniform(-0.05, 0.05)
        lng = loc[2] + random.uniform(-0.05, 0.05)
        
        c.execute('''INSERT INTO users (full_name,email,phone,password_hash,role,gender,age,address,latitude,longitude,is_available,is_verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,1)''',
            (name,email,phone,password_hash,'volunteer',gender,age,loc[0],lat,lng))
            
    # 3. Blood bank inventories
    print("Generating Inventory Data...")
    bbs = c.execute("SELECT id FROM users WHERE role='blood_bank'").fetchall()
    donors = c.execute("SELECT id FROM users WHERE role='donor'").fetchall()
    hospitals = c.execute("SELECT id FROM users WHERE role='hospital'").fetchall()
    vols = c.execute("SELECT id FROM users WHERE role='volunteer'").fetchall()
    
    today = date.today()
    
    for bb in bbs:
        bb_id = bb[0]
        # Generate 15-20 units per blood bank
        for _ in range(random.randint(15, 20)):
            bg = random.choice(BLOOD_GROUPS)
            uid = str(uuid.uuid4())[:12].upper()
            d_id = random.choice(donors)[0]
            
            # Collection date between 1 and 30 days ago
            coll_days = random.randint(1, 30)
            coll_date = today - timedelta(days=coll_days)
            exp_date = coll_date + timedelta(days=42)
            
            c.execute('INSERT INTO blood_units (unit_uid,blood_group,source,donor_id,blood_bank_id,collection_date,expiry_date,status) VALUES (?,?,?,?,?,?,?,?)',
                      (uid, bg, 'donation', d_id, bb_id, str(coll_date), str(exp_date), 'Available'))
                      
        # Update bb_inventory
        for bg in BLOOD_GROUPS:
            cnt = c.execute('''SELECT COUNT(*) FROM blood_units WHERE blood_bank_id=? AND blood_group=? AND status='Available' AND expiry_date >= ?''', (bb_id, bg, str(today))).fetchone()[0]
            c.execute('INSERT OR REPLACE INTO bb_inventory (blood_bank_id,blood_group,units,threshold_units,updated_at) VALUES (?,?,?,10,CURRENT_TIMESTAMP)', (bb_id, bg, cnt))
            
    # Recalculate global inventory
    for bg in BLOOD_GROUPS:
        tot = c.execute('SELECT SUM(units) FROM bb_inventory WHERE blood_group=?', (bg,)).fetchone()[0] or 0
        c.execute('INSERT OR REPLACE INTO inventory (blood_group,units,threshold_units,updated_at) VALUES (?,?,20,CURRENT_TIMESTAMP)', (bg, tot))
        
    print("Generating Operational Data (Requests, Accepts, Delivered, Collected)...")
    
    # 4. Generate Requests
    stat_list = ['Pending', 'Approved', 'Allocated', 'Fulfilled', 'Closed']
    priorities = ['Low', 'Medium', 'High', 'Critical']
    
    new_requests = []
    for _ in range(15):
        hosp = random.choice(hospitals)[0]
        bg = random.choice(BLOOD_GROUPS)
        units = random.randint(1, 4)
        pri = random.choice(priorities)
        patient = get_random_name(random.choice(['Male', 'Female']))
        status = random.choice(stat_list)
        bb = random.choice(bbs)[0]
        
        c.execute('''INSERT INTO blood_requests (hospital_id,blood_group,units,priority,patient_name,location,note,status,blood_bank_id)
            VALUES (?,?,?,?,?,?,?,?,?)''', (hosp, bg, units, pri, patient, 'Ward ' + str(random.randint(1,10)), 'Mock request', status, bb))
        req_id = c.lastrowid
        new_requests.append((req_id, status))
        
    # Generate volunteer tasks for some requests (Approved, Allocated, Fulfilled, Closed)
    for req_id, status in new_requests:
        if status in ['Approved', 'Allocated', 'Fulfilled', 'Closed']:
            vol = random.choice(vols)[0]
            if status == 'Approved':
                t_status = 'Assigned'
                coll = None; deli = None
            elif status == 'Allocated':
                t_status = 'Collected'
                coll = str(today); deli = None
            else:
                t_status = 'Delivered'
                coll = str(today - timedelta(days=1)); deli = str(today)
                
            c.execute('INSERT INTO volunteer_tasks (request_id,volunteer_id,task_type,status,pickup_location,delivery_location,assigned_at,collected_at,delivered_at) VALUES (?,?,?,?,?,?,?,?,?)',
                  (req_id, vol, 'delivery', t_status, 'Blood Bank Center', 'Hospital ER', str(today - timedelta(days=random.randint(1,5))), coll, deli))
                  
    # Add Donations
    for _ in range(20):
        d_id = random.choice(donors)[0]
        h_id = random.choice(hospitals)[0]
        v_id = random.choice(vols)[0]
        bg = c.execute("SELECT blood_group FROM users WHERE id=?", (d_id,)).fetchone()[0] or random.choice(BLOOD_GROUPS)
        
        # 'requested', 'approved', 'in_progress', 'completed'
        status = random.choice(['requested', 'approved', 'in_progress', 'completed'])
        
        c.execute('''INSERT INTO donations (donor_id,hospital_id,volunteer_id,blood_group,units,status,requested_by,chk_age_weight,chk_tattoos,chk_symptoms,chk_surgery)
            VALUES (?,?,?,?,?,?,?,1,1,1,1)''',
            (d_id, h_id, v_id, bg, 1, status, 'donor'))
            
    db.commit()
    db.close()
    print("Database seeding completed.")

if __name__ == '__main__':
    seed_data()
