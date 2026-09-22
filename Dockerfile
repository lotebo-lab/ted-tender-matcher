# Official Apify base image for Python Actors without a browser.
# https://docs.apify.com/platform/actors/development/actor-definition/dockerfile
FROM apify/actor-python:3.13

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Installing dependencies:" \
 && pip install --no-cache-dir -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

# Fail the build early if the source does not even compile.
RUN python -m compileall -q src

CMD ["python", "src/main.py"]
