# Installing workshop prerequisites

1. Clone this repository
2. Create a virtual environment
   ```shell
   python -m venv venv
   . venv/bin/activate
   ```
3. Install the ollama tool through https://ollama.com or via your package manager.
   * **Note:** some package managers split versions for various GPU. For example, in Arch Linux: `ollama` = cpu only, 
`ollama-cuda` = nvidia, `ollama-rocm` = amd.
4. Download the ollama models. For low-bandwidth (at venue) installation, see option below.
   1. Run ollama server
      ```shell 
      ollama serve
      ```
   1. Pull the workshop models (12 GB total)
      ```shell
      ollama pull qwen3
      ollama pull gemma4
      # test the models
      echo hello | ollama run qwen3
      echo hello | ollama run gemma4
      ```
5. Install python dependencies (small download)
   1. If you have the `uv` tool
      ```shell
      uv pip install -r requirements.txt
      ```
   1. Otherwise use standard `pip`
      ```shell
      pip install -r requirements.txt
      ```
6. Test that you can run the sniffer
   ```shell
   ./sniffer.py
   ```
   This opens the tool, press `q` to quit.
7. Optional: import the project into your IDE
   * If you don't use an IDE, you can edit the files with notepad
8. Optional: if you already use a coding harness, integrate it with ollama and test
   ```shell
   # launch supported tools with ollama integration
   ollama launch
   ```

# Low-bandwidth installation (at venue)

Complete steps 1-3 from the normal procedure. Then, replace the model download (step 4) with this instead:

4. Copy the ollama models from flash disk
   1. Run ollama server once and stop it
      ```shell 
      ollama serve
      # wait, then press ctrl-c
      ```
   1. Locate your ollama directory
      * Linux/Mac: `~/.ollama`
      * Windows: `C:\users\%username%\.ollama`
   1. Copy the models (12 GB total)
      * Unpack `ollama-models.tar` to your ollama directory (merge models from tar with your models sub-dir)
         ```shell
         cd ~/.ollama ; tar xvvf /path/to/ollama-models.tar
         ```
   1. Run ollama server and test
      ```shell 
      ollama serve
      # test the basic models
      echo hello | ollama run qwen3
      echo hello | ollama run gemma4
5. Continue with step 5 from the instructions

# Licensing

Example scripts and tools in this repository are authored by Michal Svoboda. They may be re-used under the 
**Apache 2.0 license** (see [LICENSE.txt](license/LICENSE.txt)).

