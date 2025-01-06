import wave
import struct
import time
import sys
import boto3
import uuid
import json
import os
import sys
from botocore.exceptions import ClientError

TERM_SIGNAL = "<TERM>"

def create_sqs_queues(sqs_client, prefix):
    """Create input and output FIFO queues with unique identifiers"""
    queue_suffix = str(uuid.uuid4())
    
    input_queue_name = f"{prefix}-input-{queue_suffix}.fifo"
    output_queue_name = f"{prefix}-output-{queue_suffix}.fifo"
    
    queue_attributes = {
        'FifoQueue': 'true',
        'ContentBasedDeduplication': 'true'
    }
    
    try:
        input_queue = sqs_client.create_queue(
            QueueName=input_queue_name,
            Attributes=queue_attributes
        )
        output_queue = sqs_client.create_queue(
            QueueName=output_queue_name,
            Attributes=queue_attributes
        )
        
        return input_queue['QueueUrl'], output_queue['QueueUrl']
    except ClientError as e:
        print(f"Error creating queues: {e}")
        sys.exit(1)


def invoke_lambda_function(lambda_client, function_name, input_queue_url, output_queue_url):
    """Invoke Lambda function with queue URLs"""
    payload = {
        'input_queue_url': input_queue_url,
        'output_queue_url': output_queue_url
    }
    
    # Check if running in local development mode
    if os.environ.get('LOCAL_DEV', '').lower() == 'true':
        payload_str = json.dumps(payload).replace("\"", "\\\"")
        command = f"python3 -c 'import sys; sys.path.append(\"whisper-mq/whisper.cpp/lambda\"); import handler; import json; handler.lambda_handler(json.loads(\"{payload_str}\"), {{}})'"
        
        print("\nTo run the lambda handler locally, copy and run this command:")
        print(f"\n{command}\n")

    else:
        # Original AWS Lambda invocation
        try:
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',
                Payload=json.dumps(payload)
            )
            
            if response['StatusCode'] not in (200, 202):
                raise Exception(f"Lambda invocation failed: {response}")
                
        except ClientError as e:
            print(f"Error invoking Lambda: {e}")
            sys.exit(1)

def send_audio_chunk(sqs_client, queue_url, chunk_data, sequence_number):
    """Send audio chunk to SQS queue"""
    try:
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=chunk_data.hex(),
            MessageGroupId='audio_stream',
            MessageDeduplicationId=f'chunk_{sequence_number}'
        )
    except ClientError as e:
        print(f"Error sending message: {e}")
        return False
    return True

def process_audio_file(wav_path, sqs_client, queue_url, chunk_size=32000):
    """Process and send audio file in chunks"""
    with wave.open(wav_path, 'rb') as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        
        chunk_duration = chunk_size / framerate
        sequence_number = 0
        
        while True:
            chunk_start_time = time.time()
            
            raw_data = wav_file.readframes(chunk_size)
            if not raw_data:
                break
                
            float_data = []
            
            format_str = '<h' if sample_width == 2 else '<l'
            scale = 32768.0 if sample_width == 2 else 2147483648.0
            
            for i in range(0, len(raw_data), sample_width):
                sample = struct.unpack(format_str, raw_data[i:i+sample_width])[0]
                float_data.append(float(sample) / scale)
            
            if channels == 2:
                float_data = [(float_data[i] + float_data[i+1]) / 2.0 
                             for i in range(0, len(float_data), 2) 
                             if i+1 < len(float_data)]
            
            float_bytes = struct.pack(f'<{len(float_data)}f', *float_data)
            
            if not send_audio_chunk(sqs_client, queue_url, float_bytes, sequence_number):
                return False
                
            sequence_number += 1
            
            processing_time = time.time() - chunk_start_time
            sleep_time = chunk_duration - processing_time
            print(f"sleeping {sleep_time}")
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    return True

def send_termination_signal(sqs_client, queue_url):
    """Send termination signal to input queue"""
    try:
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=TERM_SIGNAL,
            MessageGroupId='audio_stream',
            MessageDeduplicationId='termination_signal'
        )
        return True
    except ClientError as e:
        print(f"Error sending termination signal: {e}")
        return False

def main(wav_path):
    # AWS clients
    sqs_client = boto3.client('sqs')
    lambda_client = boto3.client('lambda')
    
    # Create queues
    input_queue_url, output_queue_url = create_sqs_queues(sqs_client, 'whisper-stream')
    
    print(f"\nUse this command to subscribe to transcriptions:")
    print(f"python3 client_realtime_transcription_v3.py --queue-url {output_queue_url}\n")
 
    # Invoke Lambda function
    invoke_lambda_function(
        lambda_client,
        'whisper_stream_processor',
        input_queue_url,
        output_queue_url
    )
    
    try:
        # Process and send audio
        if process_audio_file(wav_path, sqs_client, input_queue_url):
            send_termination_signal(sqs_client, input_queue_url)
            print(f"Audio processing complete.")
        else:
            print("Error processing audio file")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nReceived keyboard interrupt. Sending termination signal...")
        send_termination_signal(sqs_client, input_queue_url)
        print("Termination signal sent. Exiting...")
        sys.exit(0)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python client_realtime.py <wav_file>")
        sys.exit(1)
        
    main(sys.argv[1])