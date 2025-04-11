import streamlit as st
import json
import re
import os
import json
import time
from openai import AzureOpenAI, APIConnectionError, Timeout, OpenAIError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from elasticsearch import Elasticsearch, helpers, NotFoundError, TransportError, AuthenticationException
from elasticsearch.helpers import scan, bulk
import requests
import requests, json
import httpx


st.title("🏡 Find a Home")

st.sidebar.header("🔒 Sensitive Settings")

ELASTIC_CLOUD_ID = st.sidebar.text_input("Elastic Serverless CloudID", value="")
ELASTIC_API_KEY = st.sidebar.text_input("Elastic Serverless API Key", type="password", value = "")
GEOCODE_URL = st.sidebar.text_input("Google Maps Endpoint", value = "https://maps.googleapis.com/maps/api/geocode/json")
GOOGLE_MAPS_API_KEY = st.sidebar.text_input("Google Maps API Key", type="password", value = "")
AZURE_API_KEY = st.sidebar.text_input("Azure OpenAI Key", type="password", value = "")
deployment_name = st.sidebar.text_input("Azure Deployment Name", value = "gpt-4o-global")
API_VERSION = st.sidebar.text_input("Azure API Version", value = "2024-05-01-preview")
ENDPOINT = st.sidebar.text_input("Azure Endpoint", type="password", value = "")
TEMPLATE_ID="properties-search-template"
INDEX_NAME="properties"
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds between retries


try:
    client = AzureOpenAI(
        azure_endpoint=ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=API_VERSION
    )

    es = Elasticsearch(
        cloud_id=ELASTIC_CLOUD_ID,
        api_key=ELASTIC_API_KEY,
        request_timeout=300
    )

    es.info()

except:
    pass  # Silently ignore all errors




def setElasticClient():
  es = Elasticsearch(cloud_id=ELASTIC_CLOUD_ID, api_key=ELASTIC_API_KEY, request_timeout=300)
  es.info()



def setAzureClient():
  client = AzureOpenAI(azure_endpoint=ENDPOINT, api_key=AZURE_API_KEY, api_version=API_VERSION)



def find_a_home(content):

    messages = [
        {
            "role": "system",
            "content": (
                "You are an assistant that only provides home finder recommendations "
                "based on the search results retrieved from Elasticsearch. "
                "Do not make up information or answer based on assumptions. "
                "Only use the provided data to respond to the user's queries."
                "Don't make assumptions about what values to use with functions. Ask for clarification if a user request is ambiguous."
                "Provide details about the homes in valid JSON format, as one-line strings, without any markdown formatting or triple backticks. "
                "Do not wrap the output in json or , and do not include line breaks.  "
                "Seperate each home json object by comma and newline"
                "enclose each json object with <home></home>"
                "for features, add a comma per feature such as Central air Garage Carpet Flooring Central Air Cooling this converted to Central air, Garage, Carpet Flooring, Central Air Cooling"
            ),
        },
        {"role": "user", "content": content},
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "extract_home_search_parameters",
                "description": "Extract search parameters for finding homes (excluding the query itself).  the parameters are extracted from the input query",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "the full input query",
                        },
                        "distance": {
                            "type": "string",
                            "description": "The search radius (e.g., 500m, 1000m).",
                        },
                        "bedrooms": {
                            "type": "number",
                            "description": "The number of bedrooms a home may have (e.g., 2, 3, 4).  Convert text representation of numbers into numeric",
                        },
                        "bathrooms": {
                            "type": "number",
                            "description": "The number of bathrooms a home may have (e.g., 2, 2.5, 3).  Convert text representation of numbers into numeric",
                        },
                        "tax": {
                            "type": "number",
                            "description": "Tax amount.  Convert text representation of numbers into numeric",
                        },
                       "maintenance": {
                            "type": "number",
                            "description": "maintenance fees or HOA fees.  Convert text representation of numbers into numeric",
                        },
                        "location": {
                            "type": "string",
                            "description": "Location mentioned in the query (e.g., Belongil Beach, The woodlands texas).",
                        },
                        "square_footage": {
                            "type": "number",
                            "description": "Sqaure footage of home (e.g., 1200, 15000)",
                        },
                        "home_price": {
                            "type": "number",
                            "description": "The price of the home for sale",
                        },
                        "feature": {
                            "type": "string",
                            "description": "home features, amenities, or descriptive terms (e.g., 2 car garage, pool, gym, modern, luxurious). This can include multiple options.",
                        },
                    },
                    "required": ["query", "feature"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "geocode_location",
                "description": "Resolve a location to its latitude and longitude.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The name of the location, e.g., Belongil Beach, The woodlands texas.",
                        }
                    },
                    "required": ["location"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_elasticsearch",
                "description": "Query Elasticsearch for accommodations based on provided parameters from extract_home_search_parameters.  Must call extract_home_search_parameters prior to call this function ",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The original search query (e.g., 'homes near Belongil Beach').",
                        },
                        "latitude": {
                            "type": "number",
                            "description": "Latitude of the location.",
                        },
                        "longitude": {
                            "type": "number",
                            "description": "Longitude of the location.",
                        },
                        "distance": {
                            "type": "string",
                            "description": "Search radius.  Miles should be abbreviated as mi and kilometers as km ",
                        },
                        "maintenance": {
                            "type": "number",
                            "description": "maintenance fees or HOA fees.  Convert text representation of numbers into numeric",
                        },
                        "tax": {
                            "type": "string",
                            "description": "The country name (e.g., 'Australia', 'United States').",
                        },
                        "bedrooms": {
                            "type": "string",
                            "description": "The number of bedrooms a home may have (e.g., 2, 3, 4).  Convert text representation of numbers into numeric",
                        },
                        "bathrooms": {
                            "type": "string",
                            "description": "The number of bathrooms a home may have (e.g., 2, 2.5, 3).  Convert text representation of numbers into numeric",
                        },
                        "square_footage": {
                            "type": "string",
                            "description": "Sqaure footage of home (e.g., 1200, 15000)",
                        },
                        "home_price": {
                            "type": "number",
                            "description": "Price of home for sale",
                        },
                        "feature": {
                            "type": "string",
                            "description": "home features, amenities, or descriptive terms (e.g., 2 car garage, pool, gym, modern, luxurious). This can include multiple options.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    parameters = {}
    while True:
        # Call the LLM with tools
        try:
            response = client.chat.completions.create(
                model=deployment_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as conn_err:
            print(f"⚠️ Connection error occurred: {conn_err}")
            raise ConnectionError

        except httpx.HTTPStatusError as http_err:
            print(f"❌ HTTP error (status {http_err.response.status_code}): {http_err}")
            raise ConnectionError

        except Exception as e:
            print(f"❌ Unexpected error: check your Azure OpenAI configuration. {e}")
            raise ConnectionError

        response_message = response.choices[0].message
        messages.append(response_message)

        # Print formatted messages for debugging
        print_messages([response_message])

        # Check for tool calls
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                if function_name == "extract_home_search_parameters":
                    # Debug: Print function_args
                    print("Function Arguments for extract_home_search_parameters:")
                    print(function_args)

                    # Extract required and optional parameters
                    function_response = handle_extract_home_search_parameters(
                        function_args
                    )

                    # Debug: Print function_response
                    print("Response from handle_extract_home_search_parameters:")
                    print(function_response)

                    parameters.update(json.loads(function_response))

                    # Debug: Print updated parameters
                    print("Updated parameters after extract_home_search_parameters:")
                    print(parameters)

                elif function_name == "query_elasticsearch":
                    # Ensure 'query' is present
                    if "query" not in parameters:
                        print("Error: 'query' is required for Elasticsearch queries.")
                        return None

                    print("Function Arguments for query_elasticsearch:")
                    print(function_args)

                    # Update parameters directly
                    # parameters.update(function_args)

                    # Pass extracted parameters to Elasticsearch
                    # function_response = call_elasticsearch(parameters)
                    function_response = call_elasticsearch(
                        query=function_args.get("query"),
                        latitude=function_args.get("latitude"),
                        longitude=function_args.get("longitude"),
                        feature=function_args.get("feature"),
                        distance=function_args.get("distance"),
                        bedrooms=function_args.get("bedrooms"),
                        bathrooms=function_args.get("bathrooms"),
                        tax=function_args.get("tax"),
                        maintenance=function_args.get("maintenance"),
                        home_price=function_args.get("home_price"),
                        square_footage=function_args.get("square_footage"),
                    )

                elif function_name == "geocode_location":
                    function_response = geocode_location(
                        location=function_args.get("location")
                    )
                    geo_response = json.loads(function_response)
                    parameters.update(geo_response)

                    # Debug: Print updated parameters
                    print("Updated parameters after geocode_location:")
                    print(parameters)
                else:
                    function_response = json.dumps({"error": "Unknown function"})

                # Append the tool response to the conversation
                messages.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps(function_response),
                    }
                )
        else:
            # If no further tools are requested, break the loop
            break
    return response_message
    

def format_message(message):
    """
    Format a ChatCompletionMessage for easier readability.
    """
    formatted_message = f"Role: {message.role}\n"
    if message.content:
        formatted_message += f"Content: {message.content}\n"
    if message.function_call:
        formatted_message += (
            f"Function Call:\n"
            f"  Name: {message.function_call.name}\n"
            f"  Arguments: {message.function_call.arguments}\n"
        )
    if message.tool_calls:
        formatted_message += "Tool Calls:\n"
        for tool_call in message.tool_calls:
            formatted_message += (
                f"  Tool Call ID: {tool_call.id}\n"
                f"  Function Name: {tool_call.function.name}\n"
                f"  Arguments: {tool_call.function.arguments}\n"
            )
    return formatted_message


def print_messages(messages):
    print("\nFormatted Messages:")
    for i, message in enumerate(messages, 1):
        print(f"Message {i}:")
        print(format_message(message))
        print("-" * 50)


def call_elasticsearch(
    query,
    latitude=None,
    longitude=None,
    feature=None,
    distance=None,
    bedrooms=None,
    bathrooms=None,
    tax=None,
    maintenance=None,
    home_price=None,
    square_footage=None
):
    try:
        # Construct the params dictionary
        params = {
            "query": query,
            "latitude": latitude,
            "longitude": longitude,
            "feature": feature,
            "distance": distance,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "tax": tax,
            "maintenance": maintenance,
            "home_price": home_price,
            "square_footage": square_footage,
        }

        # Remove None values
        cleaned_params = {
            key: value for key, value in params.items() if value is not None
        }

        # Debug: Print the parameters for Elasticsearch
        print("Parameters for Elasticsearch:")
        print(cleaned_params)

        # Construct the query body
        query_body = {
            "id": TEMPLATE_ID,
            "params": cleaned_params,
        }

        # Debug: Print query for Elasticsearch
        print("Elasticsearch Query:")
        print(json.dumps(query_body, indent=2))

        # Call Elasticsearch
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = es.search_template(index=INDEX_NAME, body=query_body)
                print("Elasticsearch query successful.")
                break  # Exit the loop on success
            except AuthenticationException as e:
                # Handle 401 Unauthorized
                print("❌ Authentication failed: missing or invalid credentials.")
                break  # Do not retry on auth errors
            except TransportError as e:
                if e.status_code == 408:
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    else:
                        raise  # Raise after final retry
                else:
                    raise  # Raise unexpected errors

        # Convert response to a JSON-serializable dictionary
        response_body = response.body

        # Extract and print the number of results
        total_results = response_body.get("hits", {}).get("total", {}).get("value", 0)
        print(f"Number of results found: {total_results}")

        return response_body
    
    except Exception as e:
        print(f"Error while querying Elasticsearch: {e}")
        return {"error": str(e)}


def handle_extract_home_search_parameters(args):
    """
    Validate and handle parameters extracted by the LLM.
    """
    if "latitude" in args and "longitude" in args:
        if "distance" not in args:
            args["distance"] = "5000m"  # Default distance

    return json.dumps(args)


def geocode_location(location):
    """
    Resolve a location to latitude and longitude using Google Geocoding API.
    """
    GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_MAPS_API_KEY}
    response = requests.get(GEOCODING_API_URL, params=params)
    if response.status_code == 200:
        data = response.json()
        if data["status"] == "OK":
            result = data["results"][0]["geometry"]["location"]
            return json.dumps({"latitude": result["lat"], "longitude": result["lng"]})
    return json.dumps({"error": "Geocoding failed"})



# Sidebar - Button
if st.sidebar.button("Set Variables"):
    setElasticClient()
    setAzureClient()


query = st.text_area(
    "Describe the home you're looking for",
    height=100,
    placeholder="e.g., within 20 miles of clearwater beach with two bedrooms two baths, 1000 square feet,  and with water views"
)

if query:
    
    result = find_a_home(query)
    

    try:
        raw = result.content.strip()
        #st.code(raw, language="json")  # Debug

        matches = re.findall(r"<home>(.*?)</home>", raw, re.DOTALL)

        for match in matches:
            home = json.loads(match)

            # Flatten fields like ["3.0"] → "3.0"
            flat = {
                k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in home.items()
            }

            st.subheader(flat.get("title", "No Title"))

            for field, label, icon in [
                ("home-price", "Home Price", "🏠"),
                ("number-of-bedrooms", "Bedrooms", "🛏"),
                ("number-of-bathrooms", "Bathrooms", "🛁"),
                ("square-footage", "Square Footage", "📐"),
                ("annual-tax", "Annual Tax", "💰"),
                ("maintenance-fee", "Maintenance Fee", "🧾"),
            ]:
                if field in flat:
                    st.markdown(f"**{icon} {label}:** {flat[field]}")

            # ✅ Handle property-features: list with a comma-separated string
            features = []
            raw_features = flat.get("property-features", "")

            if isinstance(raw_features, list) and len(raw_features) == 1:
                features_string = raw_features[0].strip()
                if features_string:
                    features = [f.strip() for f in features_string.split(",") if f.strip()]
            elif isinstance(raw_features, str):
                features = [f.strip() for f in raw_features.split(",") if f.strip()]

            # ✅ Render the features grid
            if features:
                st.markdown("**✨ Features:**")
                num_columns = 4
                for i in range(0, len(features), num_columns):
                    row = features[i:i + num_columns]
                    cols = st.columns(len(row))
                    for j, feature in enumerate(row):
                        with cols[j]:
                            st.markdown(f"`{feature}`")
            else:
                st.markdown("_No features listed._")

            st.markdown("---")

    except Exception as e:
        st.error(f"Something went wrong: {e}")










