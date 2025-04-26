#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "rich",
#   "browser-cookie3",
#   "requests",
# ]
# ///

"""
A rudimentary URL downloader (like wget or curl) to demonstrate Rich progress bars.
"""

import string
import signal
import random
import pathlib
import os.path
import argparse
import mimetypes

from threading import Event
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional, Any

from urllib.parse import urlparse

from email.headerregistry import ContentDispositionHeader

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

import requests
import browser_cookie3

progress = Progress(
    TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
    BarColumn(bar_width=None),
    "[progress.percentage]{task.percentage:>3.1f}%",
    "•",
    DownloadColumn(),
    "•",
    TransferSpeedColumn(),
    "•",
    TimeRemainingColumn(),
)


class DestinationDoesNotExist(Exception):
    pass


class DestinationIsNotDirectory(Exception):
    pass


class HTTPResponse4xx(Exception):
    pass


class HTTPResponse5xx(Exception):
    pass


def randomword(length: int):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))


def find_next_filename(filename: str):
    if not os.path.isfile(filename):
        return filename
    for i in range(1, 1000):
        if not os.path.isfile(f"{filename}.{i}"):
            return f"{filename}.{i}"
    return f"{filename}.{randomword(16)}"


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"

done_event = Event()

cookiejar = browser_cookie3.firefox()


def directory_path(pathname: str):
    pathobj = pathlib.Path(pathname)
    if not pathobj.exists():
        raise DestinationDoesNotExist("Destination dir does not exist")

    if not pathobj.is_dir():
        raise DestinationIsNotDirectory("Destination must be a directory")

    return pathobj


def handle_sigint(signum: int, frame: Any):
    done_event.set()


signal.signal(signal.SIGINT, handle_sigint)


def filename_from_content_disposition(header_value: str):
    header = ContentDispositionHeader()
    params = dict(header.value_parser(header_value).params)
    return params["filename"]


def copy_url(
    task_id: TaskID, url: str, path: pathlib.Path, default_filename: str = None
) -> None:
    """Copy data from a url to a local file."""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, stream=True, cookies=cookiejar
        )
    except requests.exceptions.ConnectionError:
        progress.remove_task(task_id)
        raise

    if disposition := response.headers.get("Content-Disposition", None):
        filename = filename_from_content_disposition(disposition)
        progress.update(task_id=task_id, filename=filename)
    else:
        filename = default_filename

    # This will break if the response doesn't contain content length
    if content_length := response.headers.get("Content-length", None):
        content_length = int(content_length)

    if not filename:
        content_type = response.headers.get("Content-Type").split(";")[0]
        guess_extension = mimetypes.guess_extension(content_type)
        if guess_extension:
            filename = f"index{guess_extension}"
            filename = find_next_filename(filename)
        else:
            filename = f"index.bin.{randomword(12)}"

    output_file_path = path.joinpath(filename)

    progress.update(task_id, total=content_length)
    with output_file_path.open("wb") as dest_file:
        progress.start_task(task_id)
        for data in response.iter_content(chunk_size=32767):
            dest_file.write(data)
            progress.update(task_id, advance=len(data))
            if done_event.is_set():
                return
    progress.remove_task(task_id)
    progress.console.log(f"Downloaded {url} to '{output_file_path}'")


def download(urls: Iterable[str], dest_dir: Optional[pathlib.Path]):
    """Download multiple files to the given directory."""

    future_to_url = {}

    if not dest_dir:
        dest_dir = pathlib.Path(".")

    with progress:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for url in urls:
                parsed_url = urlparse(url)
                default_filename = parsed_url.path.split("/")[-1]
                task_id = progress.add_task(
                    "download", start=False, filename=default_filename
                )
                future = pool.submit(copy_url, task_id, url, dest_dir, default_filename)
                future_to_url[future] = (url, task_id)
            for future in as_completed(future_to_url):
                try:
                    future.result()
                except requests.exceptions.ConnectionError:
                    url, task_id = future_to_url[future]
                    progress.console.log(
                        f"Failed to download '{url}': couldn't connect to server"
                    )


def main():
    parser = argparse.ArgumentParser()
    parser.register("type", "directory", directory_path)
    parser.add_argument(
        "--debug", action="store_true", default=False, help="Debug logging"
    )
    parser.add_argument(
        "--dest",
        default=".",
        type="directory",
        help="The directory to download files into",
    )
    parser.add_argument("urls", nargs="+", help="URLs to download")
    args = parser.parse_args()

    download(args.urls, dest_dir=args.dest)


if __name__ == "__main__":
    main()
