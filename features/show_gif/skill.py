"""
Feature: show_gif
Description: Displays a GIF on the screen based on a user-provided search query. Useful for adding visual flair or conveying emotions in a chat.
Autonomous Evolutionary Capability synthesized by NIGHTFALL AI.
"""

FEATURE_METADATA = {'name': 'show_gif', 'aliases': ['display gif', 'show animation', 'render gif', 'showgif'], 'description': 'Displays a GIF on the screen based on a user-provided search query. Useful for adding visual flair or conveying emotions in a chat.', 'triggers': ['show me a gif of', 'display animation for', 'find a gif about', 'render gif of', 'show a gif on screen as specified by user', 'show gif'], 'parameters': {'type': 'OBJECT', 'properties': {'query': {'type': 'STRING', 'description': "The search term or topic for the GIF (e.g., 'happy cat', 'dancing robot')."}}, 'required': ['query']}, 'created_at': 1790693040.010282, 'version': '1.0.0', 'author': 'Project Ultron Autonomous Self-Evolution Engine', 'active': True}

import os
import json
import urllib.request
import urllib.parse

def execute(**kwargs):
    query = kwargs.get('query', None)

    if not query:
        return {"error": "Please provide a search query for the GIF."}

    try:
        search_url = f"https://api.giphy.com/v1/gifs/search?api_key={os.environ.get('GIPHY_API_KEY')}&q={urllib.parse.quote(query)}&limit=1&rating=g"
        
        with urllib.request.urlopen(search_url, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))

        if data['data']:
            gif_url = data['data'][0]['images']['original']['url']
            return {
                "gif_url": gif_url,
                "query": query,
                "message": f"Here is a GIF for '{query}'."
            }
        else:
            return {"error": f"Could not find a GIF for '{query}'. Please try a different search term."}

    except Exception as e:
        return {"error": f"An error occurred while fetching the GIF: {str(e)}"}
