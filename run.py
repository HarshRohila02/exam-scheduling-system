"""Start the Admin Portal:  python run.py  ->  http://localhost:5000"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
