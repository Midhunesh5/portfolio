from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pymongo import MongoClient
from pydantic import EmailStr
from pydantic_settings import BaseSettings
from email.message import EmailMessage
import smtplib
import datetime
import logging
from functools import lru_cache
from contextlib import asynccontextmanager

from fastapi import Depends
# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Settings Management ---
class Settings(BaseSettings):
    EMAIL_ADDRESS: str
    EMAIL_PASSWORD: str
    MONGO_URI: str

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup, connect to the database
    settings = get_settings()
    app.state.mongo_client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    app.state.db = app.state.mongo_client["portfolio"]
    try:
        app.state.mongo_client.admin.command('ping')
        logging.info("Successfully connected to MongoDB.")
    except Exception as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
    yield
    # On shutdown, close the connection
    app.state.mongo_client.close()
    logging.info("MongoDB connection closed.")

app = FastAPI(lifespan=lifespan)

# Mount the 'assets' directory to serve static files like images and PDFs
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# --- CORS settings ---
# Be more specific with origins in production for better security
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Dependency to get DB ---
def get_db():
    if not hasattr(app.state, 'db'):
        raise HTTPException(status_code=503, detail="Database connection is not available.")
    return app.state.db

# --- Helper Functions ---
def send_email(subject: str, recipient: str, body: str, attachment_path: str = None, attachment_filename: str = None):
    """A helper function to send an email."""
    settings = get_settings()
    if not all([settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD]):
        logging.error("Email credentials are not configured.")
        # This will be caught by the generic exception handler in the endpoint
        raise smtplib.SMTPException("Server email configuration is incomplete.")

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = settings.EMAIL_ADDRESS
    msg['To'] = recipient
    msg.set_content(body)

    if attachment_path and attachment_filename:
        with open(attachment_path, "rb") as f:
            msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=attachment_filename)
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD)
        smtp.send_message(msg)

@app.post("/send-resume")
async def send_resume(name: str = Form(...), email: EmailStr = Form(...), db: MongoClient = Depends(get_db)):
    try:
        # Store user in database
        resume_collection = db["resume_requests"]
        resume_collection.insert_one({
            "name": name,
            "email": email,
            "timestamp": datetime.datetime.now()
        })
    except Exception as e:
        logging.error(f"Failed to insert data into MongoDB: {e}")
        # For this app, we can log the error and continue to send the email

    try:
        body = f"Hi {name},\n\nHere is my resume as requested.\n\nRegards,\nMidhunesh"
        send_email(
            subject="Your Requested Resume",
            recipient=email,
            body=body,
            attachment_path="assets/Midhunesh_G_Resume.pdf",
            attachment_filename="Midhunesh_Resume.pdf"
        )
        logging.info(f"Resume successfully sent to {email}")
        return {"message": "Resume sent successfully!"}
    except FileNotFoundError:
        logging.error("Resume PDF file not found at 'assets/Midhunesh_G_Resume.pdf'")
        raise HTTPException(status_code=500, detail="Server configuration error: The resume file could not be found.")
    except smtplib.SMTPException as e:
        logging.error(f"SMTP error while sending email to {email}: {e}")
        raise HTTPException(status_code=502, detail="There was an error with the email service. Please try again later.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in send_resume: {e}")
        raise HTTPException(status_code=500, detail="An unexpected server error occurred.")

@app.post("/contact")
async def handle_contact_form(name: str = Form(...), email: EmailStr = Form(...), message: str = Form(...), db: MongoClient = Depends(get_db)):
    try:
        # Store the message in a separate collection
        contact_collection = db["contact_messages"]
        contact_collection.insert_one({
            "name": name,
            "email": email,
            "message": message,
            "timestamp": datetime.datetime.now()
        })
    except Exception as e:
        logging.error(f"Failed to insert contact message into MongoDB: {e}")
        # We can still try to send the email even if DB fails

    try:
        body = f"You have a new message from:\n\nName: {name}\nEmail: {email}\n\nMessage:\n{message}"
        send_email(
            subject=f"New Portfolio Contact from {name}",
            recipient=get_settings().EMAIL_ADDRESS, # Sending the notification to yourself
            body=body
        )
        logging.info(f"Contact form message from {email} sent successfully.")
        return {"message": "Thank you for your message! I'll get back to you soon."}
    except smtplib.SMTPException as e:
        logging.error(f"SMTP error while sending contact form email: {e}")
        raise HTTPException(status_code=502, detail="There was an error with the email service.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in handle_contact_form: {e}")
        raise HTTPException(status_code=500, detail="An unexpected server error occurred.")

@app.get("/")
async def read_root():
    return FileResponse('index.html')
