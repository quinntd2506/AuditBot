import { GoogleGenAI, Type } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY || "" });

export interface AuditIssue {
  title: string;
  description: string;
  severity: "High" | "Medium" | "Low" | "Informational";
  classification: string;
}

export async function auditSmartContract(code: string): Promise<AuditIssue[]> {
  const response = await ai.models.generateContent({
    model: "gemini-3.1-pro-preview", // Using a capable model for code analysis
    contents: [
      {
        role: "user",
        parts: [
          {
            text: `Audit the following smart contract code for security vulnerabilities. 
            Identify issues, classify them (e.g., Reentrancy, Logic Error, Access Control), 
            and assign a severity level (High, Medium, Low, Informational).
            
            Return the results as a JSON array of objects with the following structure:
            {
              "title": "Short title of the issue",
              "description": "Detailed explanation and impact",
              "severity": "High/Medium/Low/Informational",
              "classification": "Vulnerability type"
            }
            
            Code:
            ${code}`
          }
        ]
      }
    ],
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.ARRAY,
        items: {
          type: Type.OBJECT,
          properties: {
            title: { type: Type.STRING },
            description: { type: Type.STRING },
            severity: { type: Type.STRING },
            classification: { type: Type.STRING }
          },
          required: ["title", "description", "severity", "classification"]
        }
      }
    }
  });

  try {
    return JSON.parse(response.text || "[]");
  } catch (e) {
    console.error("Failed to parse AI response:", e);
    return [];
  }
}
