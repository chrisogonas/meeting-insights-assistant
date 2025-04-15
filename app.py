import os
from flask import Flask, request, render_template, redirect, url_for, session
from werkzeug.utils import secure_filename
import utils # Import our helper functions

# Configure Flask app
app = Flask(__name__)
# In production, use a more secure secret key and manage it properly
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "a_very_secret_key_for_dev")
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024  # 300 MB limit

@app.route('/', methods=['GET'])
def index():
    """Display the upload form."""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and start transcription."""
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # 1. Upload to GCS
        gcs_destination_name = f"audio_uploads/{filename}" # Org structure in bucket
        try:
            gcs_uri = utils.upload_to_gcs(filepath, gcs_destination_name)
            print(f"File uploaded to {gcs_uri}")
        except Exception as e:
             print(f"GCS Upload failed: {e}")
             # Add user feedback (e.g., flash message)
             return redirect(url_for('index')) # Redirect back
        finally:
            # Clean up local file after upload
            if os.path.exists(filepath):
                 os.remove(filepath)

        # 2. Transcribe (Synchronous for this example - **NOT ideal for long audio**)
        # A production app would use long_running_recognize and poll/callback
        # For simplicity here, we run it directly and wait. Timeout in utils.py is critical.
        words_info, speaker_tags = utils.transcribe_gcs_with_diarization(gcs_uri)

        if words_info is None or speaker_tags is None:
            print("Transcription failed.")
             # Add user feedback
            return redirect(url_for('index')) # Redirect back

        # Store results in session for next step (simple state management)
        # In production: use a database or task queue system
        session['words_info'] = [(w.word, w.start_time.total_seconds(), w.speaker_tag) for w in words_info] # Serialize basic info
        session['speaker_tags'] = speaker_tags
        session['gcs_uri'] = gcs_uri # Maybe needed later?

        return redirect(url_for('identify_speakers'))

    return redirect(url_for('index'))

@app.route('/identify', methods=['GET'])
def identify_speakers():
    """Show form to map speaker tags to names/emails."""
    speaker_tags = session.get('speaker_tags')
    if not speaker_tags:
        return redirect(url_for('index')) # Redirect if no tags found
    return render_template('identify.html', speaker_tags=speaker_tags)


@app.route('/analyze', methods=['POST'])
def analyze_meeting():
    """Receive speaker mappings, format transcript, run Gemini, show results."""
    words_info_serializable = session.get('words_info')
    speaker_tags = session.get('speaker_tags')

    if not words_info_serializable or not speaker_tags:
         return redirect(url_for('index')) # Need data to proceed

    # Reconstruct words_info structure needed by format_transcript (approximation)
    # NOTE: This is simplified. A real app would store/retrieve more robustly.
    class WordInfo:
        def __init__(self, word, start_sec, tag):
            self.word = word
            self.start_time = type('obj', (object,), {'total_seconds': lambda: start_sec})()
            self.speaker_tag = tag
    words_info = [WordInfo(w, t, tg) for w, t, tg in words_info_serializable]


    # Get speaker mappings from form
    speaker_mapping = {} # Maps tag (int) to {'name': str, 'email': str}
    participant_emails = []
    for tag in speaker_tags:
        name = request.form.get(f'name_speaker_{tag}')
        email = request.form.get(f'email_speaker_{tag}')
        if name and email: # Basic validation
            speaker_mapping[tag] = {'name': name, 'email': email}
            participant_emails.append(email)
        else:
             # Handle missing mapping - maybe assign default name?
             speaker_mapping[tag] = {'name': f'Speaker {tag}', 'email': None}
             print(f"Warning: No mapping provided for Speaker {tag}")


    # Use only names for formatting the transcript text sent to Gemini
    name_mapping = {tag: data['name'] for tag, data in speaker_mapping.items()}

    # 3. Format Transcript
    formatted_transcript = utils.format_transcript(words_info, name_mapping)

    # 4. AI Analysis (Gemini)
    summary = utils.get_gemini_analysis(formatted_transcript, analysis_type="summary")
    action_items = utils.get_gemini_analysis(formatted_transcript, analysis_type="actions")

    # 5. Prepare Email (for display)
    email_subject = "Meeting Summary and Action Items"
    email_body = utils.prepare_email_body(summary, action_items)
    email_recipients = ", ".join(filter(None, [data['email'] for data in speaker_mapping.values()])) # Filter out None emails


    # Clear session data (optional)
    # session.pop('words_info', None)
    # session.pop('speaker_tags', None)

    # 6. Display Results
    return render_template(
        'results.html',
        raw_transcript=formatted_transcript,
        summary=summary,
        action_items=action_items,
        email_subject=email_subject,
        email_body=email_body,
        email_recipients=email_recipients
    )

# Placeholder for actual email sending - would require email library integration (e.g., smtplib, SendGrid)
# @app.route('/send_email', methods=['POST'])
# def send_email_route():
#     # Get recipients, subject, body from form/session
#     # Use an email library to send
#     # Provide feedback to user
#     pass

if __name__ == '__main__':
    # Set environment variables for GCP_PROJECT_ID, GCS_BUCKET_NAME, FLASK_SECRET_KEY
    # Make sure GOOGLE_APPLICATION_CREDENTIALS is set or gcloud auth is done.




    app.run(debug=True) # debug=True is NOT for production