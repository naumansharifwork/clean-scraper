import csv
import importlib
import json
import logging
import os
from pathlib import Path
from time import sleep
from typing import List, Literal, Optional, TypedDict
from urllib.parse import parse_qs, urlparse

import requests
import us  # type: ignore
from dotenv import load_dotenv
from pytube import Playlist, YouTube  # type: ignore
from retry import retry
from typing_extensions import NotRequired

logger = logging.getLogger(__name__)


# The default home directory, if nothing is provided by the user
CLEAN_USER_DIR = Path(os.path.expanduser("~"))
CLEAN_DEFAULT_OUTPUT_DIR = CLEAN_USER_DIR / ".clean-scraper"

# Set the home directory
CLEAN_OUTPUT_DIR = Path(os.environ.get("CLEAN_OUTPUT_DIR", CLEAN_DEFAULT_OUTPUT_DIR))

# Set the subdirectories for other bits
CLEAN_ASSETS_DIR = CLEAN_OUTPUT_DIR / "assets"
CLEAN_CACHE_DIR = CLEAN_OUTPUT_DIR / "cache"
CLEAN_DATA_DIR = CLEAN_OUTPUT_DIR / "exports"
CLEAN_LOG_DIR = CLEAN_OUTPUT_DIR / "logs"


class MetadataDict(TypedDict):
    asset_url: str
    case_id: NotRequired[str]
    name: str
    parent_page: str
    title: Optional[str]
    details: NotRequired[dict]


def create_directory(path: Path, is_file: bool = False):
    """Create the filesystem directories for the provided Path objects.

    Args:
        path (Path): The file path to create directories for.
        is_file (bool): Whether or not the path leads to a file (default: False)
    """
    # Get the directory path
    if is_file:
        # If it's a file, take the parent
        directory = path.parent
    else:
        # Other, assume it's a directory and we're good
        directory = path

    # If the path already exists, we're good
    if directory.exists():
        return

    # If not, lets make it
    logger.debug(f"Creating directory at {directory}")
    directory.mkdir(parents=True)


def fetch_if_not_cached(filename, url, throttle=0, **kwargs):
    """Download files if they're not already saved.

    Args:
        filename: The full filename for the file
        url: The URL from which the file may be downloaded.
    Notes: Should this even be in utils vs. cache? Should it exist?
    """
    create_directory(Path(filename), is_file=True)
    if not os.path.exists(filename):
        logger.debug(f"Fetching {filename} from {url}")
        response = requests.get(url, **kwargs)
        if not response.ok:
            logger.error(f"Failed to fetch {url} to {filename}")
        else:
            with open(filename, "wb") as outfile:
                outfile.write(response.content)
        sleep(throttle)  # Pause between requests
    return


def save_if_good_url(filename, url, **kwargs):
    """Save a file if given a responsive URL.

    Args:
        filename: The full filename for the file
        url: The URL from which the file may be downloaded.
    Notes: Should this even be in utils vs. cache? Should it exist?
    """
    create_directory(Path(filename), is_file=True)
    response = requests.get(url, **kwargs)
    if not response.ok:
        logger.error(f"URL {url} fetch failed with {response.status_code}")
        logger.error(f"Not saving to {filename}. Is a new year's URL not started?")
        success_flag = False
        content = False
    else:
        with open(filename, "wb") as outfile:
            outfile.write(response.content)
            success_flag = True
            content = response.content
    sleep(2)  # Pause between requests
    return success_flag, content


def write_rows_to_csv(output_path: Path, rows: list, mode="w"):
    """Write the provided list to the provided path as comma-separated values.

    Args:
        rows (list): the list to be saved
        output_path (Path): the Path were the result will be saved
        mode (str): the mode to be used when opening the file (default 'w')
    """
    create_directory(output_path, is_file=True)
    logger.debug(f"Writing {len(rows)} rows to {output_path}")
    with open(output_path, mode, newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def write_dict_rows_to_csv(
    output_path,
    headers,
    rows,
    mode="w",
    extrasaction: Literal["raise", "ignore"] = "raise",
):
    """Write the provided dictionary to the provided path as comma-separated values.

    Args:
        output_path (Path): the Path were the result will be saved
        headers (list): a list of the headers for the output file
        rows (list): the dict to be saved
        mode (str): the mode to be used when opening the file (default 'w')
        extrasaction (str): what to do if the if a field isn't in the headers (default 'raise')
    """
    create_directory(output_path, is_file=True)
    logger.debug(f"Writing {len(rows)} rows to {output_path}")
    with open(output_path, mode, newline="") as f:
        # Create the writer object
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction=extrasaction)
        # If we are writing a new row ...
        if mode == "w":
            # ... drop in the headers
            writer.writeheader()
        # Loop through the dicts and write them in one by one.
        for row in rows:
            writer.writerow(row)


def get_all_scrapers():
    """Get all the agencies that have scrapers.

    Returns: List of dicts containing agency slug and name
    """
    # Get all folders in dir
    folders = [p for p in Path(__file__).parent.iterdir() if p.is_dir()]
    # Filter out anything not in a state folder
    abbrevs = [state.abbr.lower() for state in us.states.STATES]
    state_folders = [p for p in folders if p.stem in abbrevs]
    scrapers = {}
    unwanted_files = [
        ".mypy_cache",
        "config",
    ]

    for state_folder in state_folders:
        state = state_folder.stem
        for mod_path in state_folder.iterdir():
            if not (mod_path.stem.startswith("__") or mod_path.stem in unwanted_files):
                agency_mod = importlib.import_module(f"clean.{state}.{mod_path.stem}")
                scrapers.setdefault(state, []).append(
                    {
                        "slug": f"{state}_{mod_path.stem}",
                        "agency": agency_mod.Site.name,
                    }
                )
    return scrapers


@retry(tries=3, delay=15, backoff=2)
def get_url(
    url, user_agent="Big Local News (biglocalnews.org)", session=None, **kwargs
):
    """Request the provided URL and return a response object.

    Args:
        url (str): the url to be requested
        user_agent (str): the user-agent header passed with the request (default: biglocalnews.org)
        session: a session object to use when making the request. optional
    """
    logger.debug(f"Requesting {url}")

    # Set the headers
    if "headers" not in kwargs:
        kwargs["headers"] = {}
    kwargs["headers"]["User-Agent"] = user_agent

    # Go get it
    if session is not None:
        logger.debug(f"Requesting with session {session}")
        response = session.get(url, **kwargs)
    else:
        response = requests.get(url, **kwargs)
    logger.debug(f"Response code: {response.status_code}")

    # Verify that the response is 200
    assert response.ok

    # Return the response
    return response


def get_youtube_url(url: str) -> List[str]:
    """Download a video or playlist from a YouTube URL and save it to the cache. Return the set of stream URLs to be downloaded.

    Args:
        url (str): The URL of the video or playlist to download
    """
    logger.debug(f"Requesting YouTube {url}")
    stream_urls = []

    try:
        if is_youtube_playlist(url):
            logger.debug("Detected Youtube playlist, fetching URLs")
            playlist = Playlist(url)
            for video in playlist.videos:
                stream = video.streams.get_highest_resolution()
                if stream:
                    stream_urls.append(stream.url)
        else:
            logger.debug("Detected Youtube video, fetching URL")
            video = YouTube(url)
            stream = video.streams.get_highest_resolution()
            if stream:
                stream_urls.append(stream.url)
    except Exception as e:
        logger.error(f"Error fetching YouTube content: {e}")

    return stream_urls


def is_youtube_playlist(url: str) -> bool:
    """
    Check if the given URL is a YouTube playlist URL.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if the URL is a playlist URL, False otherwise.
    """
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    # Check if 'list' query parameter exists
    if "list" in query_params:
        return True

    # Check if URL path contains '/playlist'
    if "/playlist" in parsed_url.path:
        return True

    return False


def get_credentials(keyname: str, return_error="") -> str:
    """
    Fetch credentials, where possible, for secret things.

    Args:
        keyname (str): A string, in all uppercase, for the credentials being sought.
        return_error: What to return if keyname is not found in any available sources.
    Returns:
        return_error (default empty string): What to return if keyname is not in any credentials
    """
    # Load environment variables from the .env file
    load_dotenv(os.path.join("env", ".env"))

    # Check if the keyname exists in the environment variables
    credential = os.getenv(keyname)
    if credential:
        logger.debug(f"Credentials for {keyname} found in .env file")
        return credential

    # Fallback to local credentials file
    credentials_file = "credentials.json"
    if os.path.exists(credentials_file):
        with open(credentials_file, encoding="utf-8") as infile:
            local_credentials = json.load(infile)
            if keyname in local_credentials:
                logger.debug(f"Credentials for {keyname} found in {credentials_file}")
                return local_credentials[keyname]

    logger.warning(
        f"No credentials for {keyname} were found. Returning default {return_error}"
    )
    return return_error


def get_repeated_asset_url(self, objects: List[MetadataDict]):
    """
    Check if the given list of objects contains any repeated asset URLs and returns them.

    Args:
        objects (List[MetadataDict]): A list of objects, where each object is a dictionary containing metadata.

    Returns:
        set: A set of asset URLs that are repeated in the given list of objects.
    """
    seen_urls = set()
    repeated_urls = set()
    for obj in objects:
        asset_url = obj.get("asset_url")
        if asset_url in seen_urls:
            repeated_urls.add(asset_url)
        else:
            seen_urls.add(asset_url)
    return repeated_urls


@retry(tries=3, delay=15, backoff=2)
def post_url(
    url, user_agent="Big Local News (biglocalnews.org)", session=None, **kwargs
):
    """Request the provided URL and return a response object.

    Args:
        url (str): the url to be requested
        user_agent (str): the user-agent header passed with the request (default: biglocalnews.org)
        session: a session object to use when making the request. optional
    """
    logger.debug(f"Requesting {url}")

    # Set the headers
    if "headers" not in kwargs:
        kwargs["headers"] = {}
    kwargs["headers"]["User-Agent"] = user_agent

    # Go get it
    if session is not None:
        logger.debug(f"Requesting with session {session}")
        response = session.post(url, **kwargs)
    else:
        response = requests.post(url, **kwargs)
    logger.debug(f"Response code: {response.status_code}")

    # Verify that the response is 200
    assert response.ok

    # Return the response
    return response


@retry(tries=3, delay=15, backoff=2)
def get_cookies(url, user_agent="Big Local News (biglocalnews.org)", **kwargs):
    """Request the provided URL and return cookie object.

    Args:
        url (str): the url to be requested
        user_agent (str): the user-agent header passed with the request (default: biglocalnews.org)
    """
    logger.debug(f"Requesting {url}")

    # Set the headers
    if "headers" not in kwargs:
        kwargs["headers"] = {}
    kwargs["headers"]["User-Agent"] = user_agent
    response = requests.get(url, **kwargs)

    # Verify that the response is 200
    assert response.ok

    cookies = response.cookies.get_dict()

    # Return the response
    return cookies
