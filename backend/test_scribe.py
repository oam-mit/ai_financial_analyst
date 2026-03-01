import os
import sys
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv

# Load environment variables (to get ELEVEN_LABS_API_KEY)
load_dotenv()

def transcribe_audio(file_path):
    # Initialize the ElevenLabs client
    api_key = os.getenv("ELEVEN_LABS_API_KEY")
    if not api_key:
        print("Error: ELEVEN_LABS_API_KEY not found in .env file.")
        return

    client = ElevenLabs(api_key=api_key)

    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    print(f"Opening file: {file_path}...")
    try:
        with open(file_path, "rb") as audio_file:
            print("Uploading and transcribing (this may take a few minutes for large files)...")
            
            # Call the Scribe v2 API
            transcription = client.speech_to_text.convert(
                file=audio_file,
                model_id="scribe_v2",
                timestamps_granularity="word"
            )

            # Define output text file name
            output_file = os.path.splitext(file_path)[0] + "_transcript.txt"
            
            print(f"\nTranscription complete. Writing to {output_file}...")
            
            with open(output_file, "w", encoding="utf-8") as out:
                out.write(f"Transcription for: {file_path}\n")
                out.write("-" * 50 + "\n")
                
                # Iterate through words and write to file
                for word in transcription.words:
                    start = f"{word.start:.2f}s"
                    end = f"{word.end:.2f}s"
                    line = f"[{start:>8} - {end:>8}] {word.text}\n"
                    out.write(line)
                    # We'll still print to console for feedback, but limited
                
            print("-" * 50)
            print(f"Success! Full transcript saved to: {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python test_scribe.py <path_to_audio_file>")
    else:
        file_to_process = sys.argv[1]
        transcribe_audio(file_to_process)
