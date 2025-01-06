import json
import zmq
import boto3
import subprocess
import threading
import time
import os
from difflib import SequenceMatcher

TERM_SIGNAL = "<TERM>"
NO_INPUT_TIMEOUT = 15  # seconds
NO_OUTPUT_TIMEOUT = 20  # seconds


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

def setup_zmq_sockets():
    context = zmq.Context()
    
    push_socket = context.socket(zmq.PUSH)
    push_socket.bind("ipc:///tmp/whisper_audio.sock")  # Lambda binds for sending audio
    
    pull_socket = context.socket(zmq.PULL)
    pull_socket.bind("ipc:///tmp/whisper_text.sock")   # Lambda binds for receiving text
    
    return context, push_socket, pull_socket

def monitor_process_output(process):
    """Monitor and print process stdout and stderr"""
    def stream_reader(stream, prefix):
        for line in iter(stream.readline, b''):
            print(f"{prefix}: {line.decode().strip()}")
    
    # Create threads for stdout and stderr
    stdout_thread = threading.Thread(
        target=stream_reader, 
        args=(process.stdout, "WHISPER-STDOUT")
    )
    stderr_thread = threading.Thread(
        target=stream_reader, 
        args=(process.stderr, "WHISPER-STDERR")
    )
    
    # Start threads
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()
    
    return stdout_thread, stderr_thread

def start_whisper_process():
    """Start whisper-stream-mq process"""
    try:
        process = subprocess.Popen(
            [
                "/home/ec2-user/whisper-mq/whisper.cpp/build/bin/whisper-stream-mq",
                "--model",
                "/home/ec2-user/whisper-mq/whisper.cpp/models/ggml-base.en.bin",
                "--length",
                "20000",
                "--step",
                "2000"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,  # Line buffered
            universal_newlines=False  # Keep as bytes for cross-platform compatibility
        )
        
        # Start monitoring the process output
        stdout_thread, stderr_thread = monitor_process_output(process)
        
        return process, stdout_thread, stderr_thread
    except Exception as e:
        print(f"Error starting whisper process: {e}")
        return None, None, None

def process_output_messages(pull_socket, sqs_client, output_queue_url):
    merger = TranscriptionMerger()
    received_first_output = False
    last_output_time = time.time()
    
    while True:
        try:
            text = pull_socket.recv_string(flags=zmq.NOBLOCK)
            if text:
                if text == TERM_SIGNAL:
                    # Send termination signal
                    sqs_client.send_message(
                        QueueUrl=output_queue_url,
                        MessageBody=TERM_SIGNAL,
                        MessageGroupId='transcription',
                        MessageDeduplicationId='termination_signal'
                    )
                    break
                else:
                    received_first_output = True
                    last_output_time = time.time()
                    
                    # Process text through merger
                    full_text, new_part = merger.merge_transcripts(text)
                    
                    # Create message payload
                    message_payload = {
                        'raw_transcription': text,
                        'stable': {
                            'full_transcript': full_text,
                            'new_text': new_part
                        }
                    }
                    
                    sqs_client.send_message(
                        QueueUrl=output_queue_url,
                        MessageBody=json.dumps(message_payload),
                        MessageGroupId='transcription',
                        MessageDeduplicationId=str(time.time())
                    )
                    
        except zmq.Again:
            # Check for output timeout
            check_timeout = time.time() - last_output_time
            if received_first_output and (check_timeout > NO_OUTPUT_TIMEOUT):
                print(f"No output received for {NO_OUTPUT_TIMEOUT} seconds, terminating process")
                # Send TERM to output SQS to notify client
                sqs_client.send_message(
                    QueueUrl=output_queue_url,
                    MessageBody=TERM_SIGNAL,
                    MessageGroupId='transcription',
                    MessageDeduplicationId='termination_signal'
                )
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"Error processing output: {e}")
            break

def cleanup(context, push_socket, pull_socket, process, stdout_thread, stderr_thread, sqs_client, input_queue_url):
    """Cleanup resources"""
    try:
        if process:
            process.terminate()
            process.wait(timeout=5)
            
        if stdout_thread:
            stdout_thread.join(timeout=1)
        if stderr_thread:
            stderr_thread.join(timeout=1)

        push_socket.close()
        pull_socket.close()
        context.term()
        
        sqs_client.delete_queue(QueueUrl=input_queue_url)
    except Exception as e:
        print(f"Error during cleanup: {e}")

def cleanup_existing_sockets():
    """Clean up any existing socket files from previous runs"""
    socket_files = [
        "/tmp/whisper_audio.sock",
        "/tmp/whisper_text.sock"
    ]
    
    for socket_file in socket_files:
        try:
            if os.path.exists(socket_file):
                os.remove(socket_file)
                print(f"Removed existing socket file: {socket_file}")
        except Exception as e:
            print(f"Error removing socket file {socket_file}: {e}")


def lambda_handler(event, context):
    # Clean up any existing socket files first
    cleanup_existing_sockets()

    # Initialize AWS client
    sqs_client = boto3.client('sqs')
    
    # Extract queue URLs from event
    input_queue_url = event['input_queue_url']
    output_queue_url = event['output_queue_url']
    
    # Setup ZMQ and whisper process
    zmq_context, push_socket, pull_socket = setup_zmq_sockets()
    print("ZMQ sockets are ready")
    whisper_process, stdout_thread, stderr_thread = start_whisper_process()
    print("whisper_process started")

    if not whisper_process:
        return {'statusCode': 500, 'body': 'Failed to start whisper process'}
    
    # Setup output processing thread
    should_continue = [True]
    output_thread = threading.Thread(
        target=process_output_messages,
        args=(pull_socket, sqs_client, output_queue_url)
    )
    output_thread.start()
    
    # Process input messages
    try:
        last_message_time = time.time()
        while True:
            response = sqs_client.receive_message(
                QueueUrl=input_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=1
            )
            
            current_time = time.time()
            
            if 'Messages' in response:
                print(f"Received SQS message {current_time}")
                message = response['Messages'][0]
                receipt_handle = message['ReceiptHandle']
                
                if message['Body'] == TERM_SIGNAL:
                    print("Received TERM signal, notifying whisper")
                    push_socket.send_string(TERM_SIGNAL)
                    
                    # Delete processed message
                    sqs_client.delete_message(
                        QueueUrl=input_queue_url,
                        ReceiptHandle=receipt_handle
                    )
                    # Now wait for output_thread to receive the TERM response
                    break
                    
                # Replace the "Process audio data" try-block with this:
                # Process audio data
                try:
                    audio_data = bytes.fromhex(message['Body'])
                    send_success = False
                    retry_start_time = current_time
                    
                    while not send_success:
                        try:
                            push_socket.send(audio_data)
                            send_success = True
                            last_message_time = current_time
                        except zmq.Again:
                            # Check if we've hit the no input timeout while retrying
                            if time.time() - last_message_time > NO_INPUT_TIMEOUT:
                                print("No input timeout reached while retrying send, initiating termination")
                                push_socket.send_string(TERM_SIGNAL)
                                break
                            print("Socket buffer full - retrying")
                            time.sleep(0.1)  # Small sleep to prevent tight loop
                        except Exception as e:
                            print(f"Error sending message: {e}")
                            break
                    
                    if not send_success:
                        break  # Exit the main loop if we couldn't send due to timeout
                        
                except Exception as e:
                    print(f"Error processing message: {e}")
                
                # Delete processed message
                sqs_client.delete_message(
                    QueueUrl=input_queue_url,
                    ReceiptHandle=receipt_handle
                )
            elif current_time - last_message_time > NO_INPUT_TIMEOUT:
                print("No input received for too long, initiating termination")
                push_socket.send_string(TERM_SIGNAL)
                # Now wait for output_thread to receive the TERM response
                break
        
        # Wait for output thread to properly finish (after receiving TERM or timing out)
        output_thread.join()  # No timeout needed since thread controls its own termination

    finally:
        print("Finishing up")
        cleanup(zmq_context, push_socket, pull_socket, whisper_process, 
                stdout_thread, stderr_thread, sqs_client, input_queue_url) 
    
    print("Cleanup done, exiting")
    return {
        'statusCode': 200,
        'body': 'Processing complete'
    }