"""Run the real yt-dlp CLI with an offline extractor and synthetic HTTPS response."""

import io
import sys
from pathlib import Path
from unittest.mock import patch

import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.networking import Response

fixture = Path(sys.argv[1]).read_bytes()
media_url = 'https://media.invalid/synthetic-fixture.mp4'
def extract(self, url):
    return {'id': 'okokokokok1', 'title': 'Offline fixture', 'duration': 1, 'live_status': 'not_live',
            'url': media_url, 'ext': 'mp4', 'protocol': 'https', 'height': 32, 'width': 32,
            'vcodec': 'mpeg4', 'acodec': 'aac'}
def open_response(self, request):
    url = request if isinstance(request, str) else request.url
    if url != media_url:
        raise AssertionError('Unexpected fixture request')
    return Response(io.BytesIO(fixture), media_url,
                    {'Content-Length': str(len(fixture)), 'Content-Type': 'video/mp4'}, status=200)
def deny_network(*args, **kwargs):
    raise AssertionError('Network is disabled for this fixture')
with patch.object(YoutubeIE, '_real_initialize', lambda self: None), patch.object(YoutubeIE, '_real_extract', extract), \
        patch.object(yt_dlp.YoutubeDL, 'urlopen', open_response), patch('socket.create_connection', deny_network):
    yt_dlp.main(sys.argv[2:])
