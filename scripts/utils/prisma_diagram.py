import matplotlib.pyplot as plt
import matplotlib.patches as patches

def create_prisma_diagram(numbers_dict):
    """Create PRISMA flow diagram"""
    fig, ax = plt.subplots(1, 1, figsize=(12, 16))
    
    # Define boxes and their positions
    boxes = {
        'identification': {
            'text': f"Records identified through\ndatabase searching\n(n = {numbers_dict['total_identified']})",
            'pos': (0.5, 0.9),
            'size': (0.3, 0.08)
        },
        'screening': {
            'text': f"Records screened\n(n = {numbers_dict['screened']})",
            'pos': (0.5, 0.7),
            'size': (0.3, 0.06)
        },
        'excluded_screening': {
            'text': f"Records excluded\n(n = {numbers_dict['excluded_screening']})",
            'pos': (0.85, 0.7),
            'size': (0.25, 0.06)
        },
        'eligibility': {
            'text': f"Full-text articles assessed\nfor eligibility\n(n = {numbers_dict['full_text_assessed']})",
            'pos': (0.5, 0.5),
            'size': (0.3, 0.08)
        },
        'excluded_eligibility': {
            'text': f"Full-text articles excluded\n(n = {numbers_dict['excluded_eligibility']})",
            'pos': (0.85, 0.5),
            'size': (0.25, 0.06)
        },
        'included': {
            'text': f"Studies included in\nqualitative synthesis\n(n = {numbers_dict['included']})",
            'pos': (0.5, 0.3),
            'size': (0.3, 0.08)
        }
    }
    
    # Draw boxes
    for box_name, box_info in boxes.items():
        rect = patches.Rectangle(
            (box_info['pos'][0] - box_info['size'][0]/2, 
             box_info['pos'][1] - box_info['size'][1]/2),
            box_info['size'][0], box_info['size'][1],
            linewidth=2, edgecolor='black', facecolor='lightblue'
        )
        ax.add_patch(rect)
        ax.text(box_info['pos'][0], box_info['pos'][1], box_info['text'],
                ha='center', va='center', fontsize=10, weight='bold')
    
    # Draw arrows
    arrows = [
        ((0.5, 0.86), (0.5, 0.76)),  # identification to screening
        ((0.5, 0.64), (0.5, 0.58)),  # screening to eligibility
        ((0.5, 0.42), (0.5, 0.38)),  # eligibility to included
        ((0.65, 0.7), (0.72, 0.7)),  # screening to excluded
        ((0.65, 0.5), (0.72, 0.5)),  # eligibility to excluded
    ]
    
    for start, end in arrows:
        ax.annotate('', xy=end, xytext=start,
                   arrowprops=dict(arrowstyle='->', lw=2, color='black'))
    
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_title('PRISMA Flow Diagram', fontsize=16, weight='bold', pad=20)
    
    plt.tight_layout()
    return fig

# Example usage
prisma_numbers = {
    'total_identified': 1250,
    'screened': 1100,
    'excluded_screening': 900,
    'full_text_assessed': 200,
    'excluded_eligibility': 150,
    'included': 50
}

fig = create_prisma_diagram(prisma_numbers)
plt.show()