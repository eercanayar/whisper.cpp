import boto3
import argparse
import sys
import json
from botocore.exceptions import ClientError

TERM_SIGNAL = "<TERM>"

def receive_transcriptions(queue_url):
    """Continuously receive and print transcriptions from SQS queue"""
    sqs_client = boto3.client('sqs')
    
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
                        # Process message
                        try:
                            transcription = message['Body']
                            # Delete processed message
                            receipt_handle = message['ReceiptHandle']
                            sqs_client.delete_message(
                                QueueUrl=queue_url,
                                ReceiptHandle=receipt_handle
                            )
                                
                            if transcription:
                                if transcription==TERM_SIGNAL:
                                    print("...Transcription is finished.")
                                    sqs_client.delete_queue(QueueUrl=queue_url)
                                    sys.exit(0)
                                print(f"> {transcription}")

                            
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