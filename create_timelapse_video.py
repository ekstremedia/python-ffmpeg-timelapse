import json
import os
import glob
import yaml
import logging
import subprocess
from datetime import datetime, timedelta
from argparse import ArgumentParser
from colored import fg, attr
from tqdm import tqdm

# Load config.yaml
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

# Ensure the log directory exists
log_folder = config['log']['folder']
if not os.path.exists(log_folder):
    os.makedirs(log_folder)

# Set up logging based on config (file logging)
logging.basicConfig(
    filename=os.path.join(log_folder, config['log']['filename']),
    level=config['log']['level'],
    format=config['log']['format'],
    datefmt=config['log']['datefmt']
)

logger = logging.getLogger(__name__)

# Console logger
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(config['log']['format'], config['log']['datefmt'])
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Save metadata to JSON file
def save_metadata_to_json(output_path, metadata):
    json_filename = output_path.replace(config['filename']['extension'], '.json')
    with open(json_filename, 'w') as json_file:
        json.dump(metadata, json_file, indent=4)
    log_with_color(f"Metadata saved to {json_filename}", "info", "green")


# Sanitize metadata for filenames
def sanitize_for_filename(value):
    return str(value).replace(' ', '_').replace(':', '_').replace('/', '_')

# Append metadata to filename if required
def append_metadata_to_filename(base_filename):
    metadata = []

    if config['filename']['append_metadata']:
        metadata.append(f"filter-{sanitize_for_filename(config['video_output']['video_filter'])}")
        metadata.append(f"codec-{sanitize_for_filename(config['video_output']['codec'])}")
        metadata.append(f"crf-{sanitize_for_filename(config['video_output']['crf'])}")
        metadata.append(f"preset-{sanitize_for_filename(config['video_output']['preset'])}")
        metadata.append(f"bitrate-{sanitize_for_filename(config['video_output']['max_bitrate'])}")
        metadata.append(f"size-{sanitize_for_filename(config['video_output']['video_size'])}")

    # Join metadata with underscores and append to the filename
    if metadata:
        metadata_str = "_".join(metadata)
        base_filename = f"{base_filename}_{metadata_str}"

    return base_filename

# Progress parser helper function
def parse_ffmpeg_progress(ffmpeg_process, total_frames):
    progress_bar = tqdm(total=total_frames, desc="Encoding Progress", unit="frame")

    while True:
        line = ffmpeg_process.stdout.readline().decode("utf-8").strip()
        if not line:
            break
        if line.startswith("frame="):
            frame = int(line.split("=")[1].strip())
            progress_bar.update(frame - progress_bar.n)

    progress_bar.close()

# Helper function to log with color to console
def log_with_color(message, level="info", color="white"):
    color_code = fg(color)
    reset = attr('reset')
    if level == "info":
        logger.info(f"{color_code}{message}{reset}")
    elif level == "error":
        logger.error(f"{color_code}{message}{reset}")
    elif level == "warning":
        logger.warning(f"{color_code}{message}{reset}")

# Get image folder and file pattern based on date and config
def get_image_files(date):
    folder_structure = config['image_input']['folder_structure']
    folder = os.path.join(config['image_input']['folder'], date.strftime(folder_structure))
    search_pattern = os.path.join(folder, f"*{config['image_input']['extension']}")
    return sorted(glob.glob(search_pattern))

# Create a temporary file list for FFmpeg input
def create_ffmpeg_file_list(images):
    temp_list_file = 'ffmpeg_images.txt'
    with open(temp_list_file, 'w') as f:
        for image in images:
            f.write(f"file '{image}'\n")
    return temp_list_file

# Build FFmpeg command
def build_ffmpeg_command(image_list_file, output_path):
    ffmpeg_command = [
        # 'ffmpeg', '-y', '-r', str(config['video_output']['fps']),
        'ffmpeg', '-y', '-loglevel', 'error', '-hide_banner',  # Suppress warnings and banner
        '-f', 'concat', '-safe', '0', '-i', image_list_file,
        '-vf', config['video_output']['video_filter'],
        '-c:v', config['video_output']['codec'],
        '-crf', str(config['video_output']['crf']),
        '-preset', config['video_output']['preset'],
        '-b:v', config['video_output']['max_bitrate'],
        '-minrate', config['video_output']['min_bitrate'],
        '-maxrate', config['video_output']['max_bitrate'],
        '-bufsize', config['video_output']['buffer_size'],
        '-s', config['video_output']['video_size'],
    ]

    # Apply pixel format for specific codecs
    if config['video_output']['codec'] in ['h264_v4l2m2m', 'libx264', 'libx265']:
        ffmpeg_command.append('-pix_fmt')
        ffmpeg_command.append('yuv420p')
    
        # Set color range explicitly to full or limited (depends on your use case)
        ffmpeg_command.append('-color_range')
        ffmpeg_command.append('tv')  # Options: 'tv' (limited range) or 'pc' (full range)

    ffmpeg_command.append(output_path)
    
    return ffmpeg_command

# Create a timelapse video
def create_timelapse(date):
    try:
        start_time = datetime.now()
        log_with_color(f"Creating timelapse for date: {date}", "info", "green")

        # Apply morning-to-morning logic if set in config
        images = []
        if config['image_input']['morning_to_morning']:
            log_with_color("Applying morning-to-morning logic", "info", "cyan")

            # Define start and end times
            morning_time = datetime.strptime(config['image_input']['morning_time'], "%H:%M").time()
            start_time_morning = datetime.combine(date, morning_time)
            end_time_morning = start_time_morning + timedelta(days=1)

            # Get images from today starting at morning_time
            today_images = get_image_files(date)
            today_filtered_images = [img for img in today_images if datetime.fromtimestamp(os.path.getmtime(img)) >= start_time_morning]

            # Get images from tomorrow up to morning_time
            tomorrow_images = get_image_files(date + timedelta(days=1))
            tomorrow_filtered_images = [img for img in tomorrow_images if datetime.fromtimestamp(os.path.getmtime(img)) <= end_time_morning]

            # Combine images from today and tomorrow
            images = today_filtered_images + tomorrow_filtered_images
        else:
            # Get images from the selected date if morning-to-morning is not enabled
            images = get_image_files(date)

        if not images:
            log_with_color(f"No images found for the selected date: {date}", "error", "red")
            return

        # If test-amount is specified, limit the number of images
        if args.test_amount:
            log_with_color(f"Using only the first {args.test_amount} images for testing", "info", "magenta")
            images = images[:args.test_amount]

        # Create temporary file list for FFmpeg
        image_list_file = create_ffmpeg_file_list(images)

        # Define output path for video
        output_folder = os.path.join(config['video_output']['folder'], date.strftime(config['video_output']['folder_structure']))
        os.makedirs(output_folder, exist_ok=True)

        # Base filename before adding metadata
        base_filename = f"{config['filename']['prefix']}{date.strftime('%Y_%m_%d')}{config['filename']['suffix']}"

        # Append metadata if necessary
        output_filename = append_metadata_to_filename(base_filename)

        # Add file extension
        output_filename = f"{output_filename}{config['filename']['extension']}"
        output_path = os.path.join(output_folder, output_filename)
        
        # Create FFmpeg command with progress output
        ffmpeg_command = build_ffmpeg_command(image_list_file, output_path)
        ffmpeg_command.extend(['-progress', '-', '-nostats'])

        log_with_color(f"Running FFmpeg command: {' '.join(ffmpeg_command)}", "info", "cyan")
        
        # Run FFmpeg with progress bar
        with subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            parse_ffmpeg_progress(process, total_frames=len(images))

        # End time and calculate duration
        end_time = datetime.now()
        duration = end_time - start_time

        # Get the size of the output file
        file_size = os.path.getsize(output_path) / (1024 * 1024)  # Convert to MB

        log_with_color(f"Timelapse created successfully: {output_path}", "info", "green")
        log_with_color(f"Timelapse duration: {duration}", "info", "blue")
        log_with_color(f"Output file size: {file_size:.2f} MB", "info", "blue")

        # Collect metadata for the JSON file
        if config['metadata']['save_to_file']:
            metadata = {
                "date": date.strftime("%Y-%m-%d"),
                "output_file": output_path,
                "file_size_MB": f"{file_size:.2f}",
                "duration": str(duration),
                "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "number_of_images": len(images),
                "image_input_folder": config['image_input']['folder'],
                "video_filter": config['video_output']['video_filter'],
                "codec": config['video_output']['codec'],
                "crf": config['video_output']['crf'],
                "preset": config['video_output']['preset'],
                "bitrate": config['video_output']['max_bitrate'],
                "video_size": config['video_output']['video_size'],
                "test_amount": args.test_amount if args.test_amount else "full"
            }
            # Save metadata to JSON
            save_metadata_to_json(output_path, metadata)

    except Exception as e:
        log_with_color(f"Error creating timelapse: {e}", "error", "red")


# Argument parsing
parser = ArgumentParser(description="Create a timelapse video from images")
parser.add_argument('--date', type=str, help="Specify a date (YYYY-MM-DD) for timelapse")
parser.add_argument('--test-amount', type=int, help="Limit the number of images to use for a quick test timelapse")

args = parser.parse_args()

# If no date is provided, use today's date
if args.date:
    selected_date = datetime.strptime(args.date, '%Y-%m-%d').date()
else:
    selected_date = datetime.today().date()

# Create the timelapse
create_timelapse(selected_date)
