# QuestGen-AI

QuestGen-AI is an advanced exam question generation platform that leverages AI-Agent to automatically create customized question papers from user-uploaded PDF content. Built for educators and assessment creators, QuestGen-AI streamlines the often time-consuming process of creating high-quality assessment materials.

https://github.com/user-attachments/assets/bc8cec24-a104-4f22-9e8d-92a6568f6ea2

## 🚀 Features

- **AI-Powered Question Generation**: Creates high-quality, contextually relevant questions from PDF content using advanced language models
- **Multiple Question Types**: Supports various question formats including multiple-choice, true/false, short answer, and essay questions
- **Customizable Parameters**: Set question paper parameters such as marks, difficulty levels, and question distribution
- **Institutional Branding**: Include custom headers with institution name, course details, and exam information
- **PDF Document Processing**: Upload and extract content from PDF study materials, textbooks, or lecture notes
- **Real-time Generation**: Watch as your question paper is created through a streaming interface
- **Multi-Agent Architecture**: Utilizes specialized AI agents for extraction, creation, analysis, and formatting of questions
- **Markdown Export**: Generated question papers are formatted in clean, structured Markdown for easy export to other formats

## 🛠️ Technology Stack

- **Frontend**: Next.js, React, TailwindCSS, Framer Motion
- **Backend**: Next.js API routes
- **AI Integration**: LangChain, OpenRouter API (compatible with various LLM models)
- **PDF Processing**: PDF.js, pdf-parse
- **Styling**: Custom UI components with Tailwind CSS and Framer Motion animations
- **Streaming**: Server-Sent Events (SSE) for real-time content streaming

## 📋 Requirements

- Node.js (v18.0 or later)
- npm or yarn package manager
- OpenRouter API key (for accessing AI models)
- Modern web browser

## 🔧 Installation & Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/yourusername/QuestGen-AI.git
   cd QuestGen-AI
   ```

2. **Install dependencies**

   ```bash
   npm install
   # or
   yarn install
   ```

3. **Set up environment variables**

   Create a `.env.local` file in the project root with:

   ```
   NEXT_PUBLIC_OPENROUTER_SITE_URL="http://localhost:3000"
   OPENROUTER_API_BASE="https://openrouter.ai/api/v1"
   ```

4. **Run the development server**

   ```bash
   npm run dev
   # or
   yarn dev
   ```

5. **Access the application**

   Open [http://localhost:3000](http://localhost:3000) in your web browser

## 💻 Usage Guide

### Generating an Exam Paper

1. **Navigate to the Question Generator page**

   From the homepage, click "Start For Free" or navigate to `/chat` route.

2. **Upload Content**

   Use the file upload area to drag and drop or select PDF documents containing the content from which questions should be generated.

3. **Enter Question Paper Parameters**

   - **Question Header**: Enter institutional information, exam details, and course information

     - Example: "University of Technology - Midterm Examination - CS101: Introduction to Programming - Spring 2023"

   - **Question Description**: Provide instructions and parameters for the question paper
     - Example: "Create a question paper with 5 multiple-choice questions (2 marks each), 3 short-answer questions (5 marks each), and 1 essay question (10 marks). Focus on Python basics and data structures."

4. **Enter API Key**

   Provide your OpenRouter API key in the designated field. This is used to access the AI models that power the question generation.

5. **Select Model (Optional)**

   Choose the AI model to use for generation. The default is "qwen/qwq-32b:free".

6. **Generate Questions**

   Click the "Generate Question Paper" button to start the process. The system will:

   - Process your PDF documents
   - Extract relevant content
   - Analyze requirements
   - Create appropriate questions
   - Format a complete exam paper

7. **View and Export Results**

   Once generation is complete, you can:

   - Review the generated questions
   - Copy the content
   - Print or save the question paper

## 🧠 How It Works

![image](https://github.com/user-attachments/assets/1cfdb5fc-a541-4875-8c56-5b03545bed93)

QuestGen-AI employs a sophisticated multi-agent system powered by LangChain and large language models:

1. **Extractor Agent**: Analyzes your input requirements to understand the exam type, total marks, question types, and subject areas

2. **Question Creator Agent**: Generates appropriate questions based on the extracted requirements and the content of the uploaded PDFs

3. **Question Analysis Agent**: Evaluates the generated questions for quality, clarity, relevance, and alignment with requirements

4. **Decider Agent**: Determines if the questions meet all requirements or need further refinement

5. **Formatter Agent**: Arranges the questions into a professionally formatted exam paper with clear sections and instructions

This multi-step process ensures high-quality, relevant questions that align with your specific needs and content.

## 📁 Project Structure

```
/
├── app/                      # Next.js app directory
│   ├── (root)/               # Root route group
│   │   ├── chat/             # Question generation page
│   │   └── page.tsx          # Home page
│   ├── api/                  # API routes
│   │   └── generate-questions/ # Question generation API
│   ├── assets/               # Static assets
│   ├── globals.css           # Global styles
│   └── layout.tsx            # Root layout component
├── components/               # Reusable React components
│   ├── eldoraui/             # Custom UI components
│   ├── ui/                   # Base UI components
│   └── About.tsx             # About component
├── lib/                      # Utility functions
│   ├── prompts/              # AI prompt templates
│   └── utils.ts              # Helper functions
├── public/                   # Public assets
├── services/                 # Core services
│   └── index.ts              # Multi-agent question generation service
├── temp/                     # Temporary directory for uploaded files
├── .env.local                # Environment variables
├── next.config.ts            # Next.js configuration
├── package.json              # Project dependencies
├── postcss.config.js         # PostCSS configuration
└── tailwind.config.ts        # Tailwind CSS configuration
```

## 🔄 API Documentation

### POST `/api/generate-questions`

Uploads PDF files and initiates question generation.

**Request**:

- HTTP Method: `POST`
- Content-Type: `multipart/form-data`
- Body:
  - `file-{index}`: PDF files
  - `questionHeader`: String - Exam header details
  - `questionDescription`: String - Question paper requirements
  - `apiKey`: String - OpenRouter API key
  - `modelName`: String (optional) - AI model name

**Response**:

- Status: `200 OK`
- Body:
  ```json
  {
    "message": "success",
    "uploadedFiles": ["1234567890.pdf"]
  }
  ```

### GET `/api/generate-questions`

Generates questions based on previously uploaded files.

**Request**:

- HTTP Method: `GET`
- Query Parameters:
  - `questionHeader`: String - Exam header details
  - `questionDescription`: String - Question paper requirements
  - `apiKey`: String - OpenRouter API key
  - `modelName`: String (optional) - AI model name
  - `uploadedFiles`: String - Comma-separated list of uploaded file names

**Response**:

- Content-Type: `text/event-stream`
- Events:
  - `data`: Contains generated content chunks
  - `error`: Contains error information if generation fails
  - `complete`: Signals generation completion

## 🔒 Security Considerations

- QuestGen-AI does not store your API keys permanently
- Uploaded PDF files are processed temporarily and deleted once question generation is complete
- No user data is retained between sessions
- API keys are transmitted securely but should still be protected carefully

## 🔮 Future Improvements

- User accounts for saving and organizing generated question papers
- Additional question formats and customization options
- Pre-built templates for common exam types
- Batch generation of multiple question papers
- Integration with learning management systems
- Offline mode using local models

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

Built with ❤️ using Next.js, LangChain, and OpenRouter
