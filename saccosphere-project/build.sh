pip install -r requirements.txt
mkdir -p logs
python manage.py collectstatic --no-input
python manage.py migrate
 
