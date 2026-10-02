import { Check } from 'lucide-react';

interface StepperProps {
  currentStep: number;
  onStepClick?: (step: number) => void;
  completedSteps?: number[];
}

export default function Stepper({ currentStep, onStepClick, completedSteps = [] }: StepperProps) {
  const steps = [
    { number: 1, label: 'Define Mapping', name: 'Projects' },
    { number: 2, label: 'Source Selection', name: 'Mapping Result' },
    { number: 3, label: 'Generate Output', name: 'DBT Code' },
  ];

  const isStepCompleted = (stepNumber: number) => {
    return completedSteps.includes(stepNumber) || stepNumber < currentStep;
  };

  const isStepClickable = (stepNumber: number) => {
    // Step is clickable if it's completed or if it's the current step or previous steps
    return onStepClick && (isStepCompleted(stepNumber) || stepNumber <= currentStep);
  };

  const handleStepClick = (stepNumber: number) => {
    if (isStepClickable(stepNumber) && onStepClick) {
      onStepClick(stepNumber);
    }
  };

  return (
    <div className="bg-white border-b border-gray-200 px-6 py-4">
      <div className="flex items-center justify-between max-w-4xl mx-auto">
        {steps.map((step, index) => (
          <div key={step.number} className="flex items-center flex-1">
            <div className="flex items-center flex-1">
              <button
                onClick={() => handleStepClick(step.number)}
                disabled={!isStepClickable(step.number)}
                className={`flex items-center gap-3 ${
                  isStepClickable(step.number) 
                    ? 'cursor-pointer hover:opacity-80 transition-opacity' 
                    : 'cursor-not-allowed'
                }`}
              >
                <div
                  className={`flex items-center justify-center w-10 h-10 rounded-full font-semibold transition-all ${
                    currentStep === step.number
                      ? 'bg-blue-600 text-white ring-4 ring-blue-100'
                      : isStepCompleted(step.number)
                      ? 'bg-green-600 text-white'
                      : 'bg-gray-200 text-gray-600'
                  }`}
                >
                  {isStepCompleted(step.number) ? (
                    <Check className="w-5 h-5" />
                  ) : (
                    step.number
                  )}
                </div>
                <div className="text-left">
                  <div
                    className={`text-sm font-semibold ${
                      currentStep === step.number
                        ? 'text-blue-600'
                        : isStepCompleted(step.number)
                        ? 'text-green-600'
                        : 'text-gray-500'
                    }`}
                  >
                    {step.label}
                  </div>
                  <div className="text-xs text-gray-500">{step.name}</div>
                </div>
              </button>
            </div>
            {index < steps.length - 1 && (
              <div className="flex-1 mx-4">
                <div
                  className={`h-1 rounded transition-all ${
                    isStepCompleted(step.number)
                      ? 'bg-green-600'
                      : currentStep > step.number
                      ? 'bg-blue-600'
                      : 'bg-gray-200'
                  }`}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
