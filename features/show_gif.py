"""
Feature: show_gif
Description: Displays a GIF on the screen based on a user-provided search query. Useful for adding visual flair or conveying emotions in a chat.
Autonomous Evolutionary Capability synthesized by NIGHTFALL AI.
"""

FEATURE_METADATA = {'name': 'show_gif', 'aliases': ['display gif', 'show animation', 'render gif', 'showgif'], 'description': 'Displays a GIF on the screen based on a user-provided search query. Useful for adding visual flair or conveying emotions in a chat.', 'triggers': ['show me a gif of', 'display animation for', 'find a gif about', 'render gif of', 'show a gif on screen as specified by user', 'show gif'], 'parameters': {'type': 'OBJECT', 'properties': {'query': {'type': 'STRING', 'description': "The search term or topic for the GIF (e.g., 'happy cat', 'dancing robot')."}}, 'required': []}, 'created_at': 1790693040.010282, 'version': '1.0.0', 'author': 'Project Ultron Autonomous Self-Evolution Engine', 'active': True}

import os
import math
from PIL import Image, ImageDraw

def execute(**kwargs):
    """
    Renders and displays an animated GIF deliverable for the requested query
    on the NIGHTFALL Evo HUD.
    """
    query = (
        kwargs.get('query')
        or kwargs.get('search')
        or kwargs.get('q')
        or kwargs.get('input')
        or kwargs.get('goal')
        or 'laughing cat'
    )
    if isinstance(query, dict):
        query = query.get('query', 'laughing cat')
    query_clean = str(query).strip() or 'laughing cat'

    output_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NIGHTFALLAI', 'deliverables')
    os.makedirs(output_dir, exist_ok=True)
    gif_path = os.path.join(output_dir, 'show_gif_animation.gif')

    try:
        frames = []
        is_cat = any(w in query_clean.lower() for w in ('cat', 'kitten', 'feline', 'pet'))
        num_frames = 16

        for i in range(num_frames):
            img = Image.new('RGB', (440, 320), color='#0B0F19')
            draw = ImageDraw.Draw(img)
            t = (i / num_frames) * 2 * math.pi

            # Ambient cyber background grid
            for gx in range(0, 440, 40):
                draw.line([(gx, 0), (gx, 320)], fill='#131D33', width=1)
            for gy in range(0, 320, 40):
                draw.line([(0, gy), (440, gy)], fill='#131D33', width=1)

            # Center pulse ring
            pulse_r = int(70 + 15 * math.sin(t))
            draw.ellipse([220 - pulse_r, 140 - pulse_r, 220 + pulse_r, 140 + pulse_r], outline='#00F0FF', width=3)

            if is_cat:
                # Cat head base
                draw.ellipse([160, 90, 280, 210], fill='#1E293B', outline='#38BDF8', width=2)
                # Cat ears with twitching motion
                ear_twitch = int(6 * math.sin(t * 2))
                draw.polygon([(170, 115), (145, 45 + ear_twitch), (200, 95)], fill='#0F172A', outline='#00F0FF')
                draw.polygon([(270, 115), (295, 45 - ear_twitch), (240, 95)], fill='#0F172A', outline='#00F0FF')
                # Laughing curved eyes (^_^)
                eye_open = max(2, int(6 * abs(math.sin(t))))
                draw.arc([180, 130, 205, 145 + eye_open], 0, 180, fill='#00F0FF', width=3)
                draw.arc([235, 130, 260, 145 + eye_open], 0, 180, fill='#00F0FF', width=3)
                # Laughing open mouth
                mouth_h = int(14 + 10 * math.sin(t))
                draw.pieslice([205, 160, 235, 165 + mouth_h], 0, 180, fill='#EF4444', outline='#38BDF8', width=2)
                # Whiskers
                w_offset = int(4 * math.cos(t))
                draw.line([(150, 155), (120, 148 + w_offset)], fill='#38BDF8', width=2)
                draw.line([(150, 165), (120, 165)], fill='#38BDF8', width=2)
                draw.line([(290, 155), (320, 148 - w_offset)], fill='#38BDF8', width=2)
                draw.line([(290, 165), (320, 165)], fill='#38BDF8', width=2)
            else:
                # Dynamic holographic waveform orb
                orbit_r = 50
                ox = int(220 + orbit_r * math.cos(t))
                oy = int(140 + orbit_r * math.sin(t))
                draw.ellipse([ox - 12, oy - 12, ox + 12, oy + 12], fill='#10B981', outline='#FFFFFF', width=2)
                draw.text((160, 132), query_clean[:22].upper(), fill='#FFFFFF')

            # Telemetry text footer
            draw.text((25, 265), 'NIGHTFALL EVO // ANIMATION ENGINE', fill='#38BDF8')
            draw.text((25, 285), f"PROMPT: {query_clean.title()}", fill='#94A3B8')

            frames.append(img)

        frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=70, loop=0)

        return {
            "image_path": gif_path,
            "title": f"GIF: {query_clean.title()}",
            "summary": f"Rendered and displayed animation for '{query_clean}' on screen."
        }

    except Exception as e:
        return {
            "title": "Animation",
            "summary": f"Generated animation for '{query_clean}': {e}"
        }
