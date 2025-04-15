import os
import time
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import aiplatform
import google.auth
from google.oauth2 import service_account

# --- Configuration ---
# Set these environment variables or replace directly (not recommended for production)
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "meeting-insights")
GCP_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0377855023")
GCP_LOCATION = os.environ.get("GCP_REGION", "us-central1") # e.g., us-central1

# Define the path to your service account key file
key_path = r"C:\CoopNET\genai\meeting-insights-assistant\keys\gen-lang-client-0377855023-2b50a063bc8f.json"

# Create credentials object
credentials = service_account.Credentials.from_service_account_file(
    key_path,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# Initialize Vertex AI
aiplatform.init(project=GCP_PROJECT_ID, location=GCP_LOCATION, credentials=credentials)
# --- /Configuration ---


def upload_to_gcs(source_file_name, destination_blob_name):
    """Uploads a file to the GCS bucket."""
    from google.cloud import storage
    storage_client = storage.Client(credentials=credentials) # Use the credentials object)
    # storage_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(source_file_name)
    return f"gs://{GCS_BUCKET_NAME}/{destination_blob_name}"


def transcribe_gcs_with_diarization(gcs_uri, min_speakers=2, max_speakers=5):
    """
    Transcribes audio from GCS using Google Cloud Speech-to-Text
    with speaker diarization.
    """
    client = speech.SpeechClient(credentials=credentials) # Use the credentials object
    audio = speech.RecognitionAudio(uri=gcs_uri)

    # Speaker Diarization Config
    # Let API estimate speaker count if min/max are reasonable,
    # or set enable_speaker_diarization=True and diarization_speaker_count=X if known
    diarization_config = speech.SpeakerDiarizationConfig(
        enable_speaker_diarization=True,
        min_speaker_count=min_speakers,
        max_speaker_count=max_speakers,
    )

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MP3, # Adjust based on your audio
        sample_rate_hertz=16000, # Adjust based on your audio
        language_code="en-US",
        diarization_config=diarization_config,
        enable_automatic_punctuation=True,
        enable_word_time_offsets=True, # Needed for diarization formatting
        model="telephony", # Or 'latest_long', 'medical_dictation', etc.
    )

    print(f"Starting transcription job for {gcs_uri}...")
    operation = client.long_running_recognize(config=config, audio=audio)

    # Set a reasonable timeout based on audio length
    try:
      response = operation.result(timeout=900) # Adjust timeout as needed
    except Exception as e:
        print(f"Error during transcription: {e}")
        # Handle cases like timeout or other API errors
        return None, None # Indicate failure

    print("Transcription finished.")

    # Process results for diarization
    result = response.results[-1] # Get the final transcript result
    words_info = result.alternatives[0].words

    # Extract unique speaker tags
    speaker_tags = sorted(list(set(word.speaker_tag for word in words_info if word.speaker_tag != 0)))

    return words_info, speaker_tags # Return words and unique speaker tags found

def format_transcript(words_info, speaker_mapping):
    """Formats the transcript with mapped speaker names."""
    transcript = ""
    current_speaker = None
    line_start_time = None

    for word_info in words_info:
        speaker_tag = word_info.speaker_tag
        speaker_name = speaker_mapping.get(speaker_tag, f"Speaker {speaker_tag}") # Fallback if mapping failed

        start_time = word_info.start_time.total_seconds()

        if speaker_name != current_speaker:
            if current_speaker is not None:
                transcript += "\n" # New line for new speaker
            timestamp = time.strftime('%H:%M:%S', time.gmtime(start_time))
            transcript += f"[{timestamp}] {speaker_name}: "
            current_speaker = speaker_name
            line_start_time = start_time

        transcript += word_info.word + " "

    return transcript.strip()


def get_gemini_analysis(transcript_text, analysis_type="summary"):
    """Gets summary or action items from Gemini Pro via Vertex AI."""
    model = aiplatform.generative_models.GenerativeModel("gemini-1.0-pro") # Or specify other Gemini model

    if analysis_type == "summary":
        prompt = f"""Analyze the following meeting transcript and generate a concise summary covering the key discussion points, decisions made, and overall outcomes. Structure the summary clearly.

Transcript:
---
{transcript_text}
---

Summary:"""
    elif analysis_type == "actions":
        prompt = f"""Analyze the following meeting transcript and extract all specific action items. For each item, identify the task description, the person assigned (owner) based on the speaker names in the transcript, and the deadline if mentioned. If an owner isn't clearly stated but implied by context (e.g., "I will send..."), assign it to the speaker who said it. List the action items clearly, one per line, preferably in the format: '[Task] - [Owner Name] - [Deadline (if specified)]'. If no action items are found, state that clearly.

Transcript:
---
{transcript_text}
---

Action Items:"""
    else:
        return "Invalid analysis type requested."

    try:
        # Set safety_settings and generation_config as needed
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 1024},
            # Add safety_settings if required by Vertex AI policies
            # safety_settings=[...]
            )
        return response.text
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        # Handle potential errors like blocked prompts, API issues, etc.
        return f"Error generating {analysis_type}: {e}"


def prepare_email_body(summary, action_items):
    """Formats the email body."""
    body = f"""Hi Team,

Here's a summary and the action items from our recent meeting:

**Summary:**
{summary}

**Action Items:**
{action_items}


Best regards,
Meeting Insights Assistant
"""
    return body