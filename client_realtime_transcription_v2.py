import boto3
import argparse
import sys
import json
from botocore.exceptions import ClientError
from difflib import SequenceMatcher
from colorama import init, Fore, Back, Style

# Initialize colorama
init()

TERM_SIGNAL = "<TERM>"

class TranscriptionMerger:
    def __init__(self):
        self.current_transcript = ""
        self.similarity_threshold = 0.85
        
    def get_similarity(self, text1, text2):
        return SequenceMatcher(None, text1, text2).ratio()
    
    def merge_transcripts(self, new_text):
        new_text = new_text.replace("[ Silence ]", "").strip()
        
        if not self.current_transcript:
            self.current_transcript = new_text
            return new_text, new_text

        # Look for the new text as a substring within the current transcript
        # but focus on the end portion
        end_portion = self.current_transcript[-len(new_text)*2:]  # Look at the last 2x portion
        
        # Find the longest common substring between the end portion and new text
        longest_common = ""
        for i in range(len(end_portion)):
            for j in range(i + 10, len(end_portion) + 1):  # Minimum 10 chars
                substring = end_portion[i:j]
                if substring in new_text:
                    if len(substring) > len(longest_common):
                        longest_common = substring

        if longest_common and len(longest_common) >= 10:
            # Find where the common part starts in both texts
            common_start_current = self.current_transcript.rfind(longest_common)
            common_start_new = new_text.find(longest_common)
            
            # Keep the text up to the common part from current transcript
            # and add the new content after the common part from new text
            merged = (self.current_transcript[:common_start_current + len(longest_common)] + 
                    new_text[common_start_new + len(longest_common):])
            
            new_content = new_text[common_start_new + len(longest_common):]
            self.current_transcript = merged
            return merged, new_content
        else:
            # If no significant overlap found, treat as new segment
            self.current_transcript = new_text
            return new_text, new_text

        return self.current_transcript, ""

def receive_transcriptions(queue_url):
    """Continuously receive and print transcriptions from SQS queue"""
    sqs_client = boto3.client('sqs')
    merger = TranscriptionMerger()
    
    print(f"Listening for transcriptions on queue: {queue_url}")
    print("Press Ctrl+C to stop...\n")
    
    try:
        while True:
            try:
                response = sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20  # Long polling
                )
                
                if 'Messages' in response:
                    for message in response['Messages']:
                        try:
                            transcription = message['Body']
                            #print(f"Received raw: {transcription}")  # Debug print
                            
                            # Delete processed message
                            receipt_handle = message['ReceiptHandle']
                            sqs_client.delete_message(
                                QueueUrl=queue_url,
                                ReceiptHandle=receipt_handle
                            )
                                
                            if transcription:
                                if transcription == TERM_SIGNAL:
                                    print("\n...Transcription is finished.")
                                    sqs_client.delete_queue(QueueUrl=queue_url)
                                    sys.exit(0)
                                
                                # Merge and update transcription
                                full_text, new_part = merger.merge_transcripts(transcription)
                                
                                if new_part:  # Only update display if there's something new
                                    # Clear the terminal
                                    print("\033[2J\033[H", end="")
                                    print("Current transcript:")
                                    
                                    # Print the full transcript with new part in green
                                    if len(new_part) < len(full_text):
                                        base_text = full_text[:-len(new_part)]
                                        print(f"{base_text}{Fore.GREEN}{new_part}{Style.RESET_ALL}")
                                    else:
                                        print(f"{Fore.GREEN}{new_part}{Style.RESET_ALL}")
                            
                        except json.JSONDecodeError:
                            print("Received malformed message")
                            continue
                
            except ClientError as e:
                print(f"Error receiving message: {e}")
                sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nStopping transcription receiver...")
        sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='Receive transcriptions from SQS queue')
    parser.add_argument('--queue-url', required=True, help='URL of the SQS queue to receive from')
    
    args = parser.parse_args()
    receive_transcriptions(args.queue_url)

if __name__ == "__main__":
    main()