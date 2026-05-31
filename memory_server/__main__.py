def main():
    import uvicorn
    uvicorn.run("memory_server.app:app", host="127.0.0.1", port=7437, log_level="info")

if __name__ == "__main__":
    main()
