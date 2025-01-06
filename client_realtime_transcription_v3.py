import boto3
import argparse
import sys
import json
from botocore.exceptions import ClientError
from colorama import init, Fore, Back, Style

# Initialize colorama
init()

TERM_SIGNAL = "<TERM>"

def receive_transcriptions(queue_url):
    sqs_client = boto3.client('sqs')
    
    print(f"Listening for transcriptions on queue: {queue_url}")
    print("Press Ctrl+C to stop...\n")
    
    try:
        while True:
            try:
                response = sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20
                )
                
                if 'Messages' in response:
                    for message in response['Messages']:
                        try:
                            body = message['Body']
                            
                            if body == TERM_SIGNAL:
                                print("\n...Transcription is finished.")
                                sqs_client.delete_queue(QueueUrl=queue_url)
                                sys.exit(0)
                            
                            # Parse JSON message
                            data = json.loads(body)
                            full_text = data['stable']['full_transcript']
                            new_text = data['stable']['new_text']
                            
                            # Update display
                            if new_text:
                                print("\033[2J\033[H", end="")
                                print("Current transcript:")
                                
                                if len(new_text) < len(full_text):
                                    base_text = full_text[:-len(new_text)]
                                    print(f"{base_text}{Fore.GREEN}{new_text}{Style.RESET_ALL}")
                                else:
                                    print(f"{Fore.GREEN}{new_text}{Style.RESET_ALL}")
                            
                            # Delete processed message
                            sqs_client.delete_message(
                                QueueUrl=queue_url,
                                ReceiptHandle=message['ReceiptHandle']
                            )
                            
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