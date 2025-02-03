# !/usr/bin/env python3
# !/usr/bin/env python2

import os
from tkinter import *

import cv2
from PIL import ImageTk, Image


class Record:
    file_path: str = ''
    label: str = ''

    def __init__(self, file_path, label):
        super().__init__()
        self.file_path = file_path
        self.label = label


class BasicGUI:
    index: int = 0
    files: [str] = []
    records: [] = []

    input_dir: str = '/home/qt/projects/aka-doc/tools/input'
    output_dir: str = '/home/qt/projects/aka-doc/tools/output'

    first_load: bool = True
    image: any
    canvas: Canvas
    canvas_image: any
    entry: Entry

    def __init__(self, master):
        self.master = master
        self.master.title("A simple GUI")
        self.master.bind('<Left>', self.key_left)
        self.master.bind('<Right>', self.key_right)
        self.master.bind('<KeyRelease>', self.key_up)
        self.master.bind('<Button-1>', self.mouse_left)

        # create frame and key listener
        self.frame = Frame(master, bg='green')
        self.frame.pack(expand=True, fill=BOTH)
        self.frame.pack()

        # create entry for folder input
        self.entry_folder = Entry(self.frame, width='600', font=('Verdana', 15))
        # self.entry_folder.insert(0, 'Images directory path...')
        self.entry_folder.insert(0, self.input_dir)
        self.entry_folder.pack()

        # create entry for output label.txt
        self.entry_output_folder = Entry(self.frame, width='600', font=('Verdana', 15))
        # self.entry_output_folder.insert(0, 'Path to label file...')
        self.entry_output_folder.insert(0, self.output_dir)
        self.entry_output_folder.pack()

        self.button_folder = Button(self.frame, text='Load folder', command=self.load_files)
        self.button_folder.pack()

    def load_canvas(self):
        # get first image
        image, width, height = self.set_image()
        self.image = image

        # create canvas
        self.canvas = Canvas(self.frame, width=width, height=height)
        self.canvas.pack()

        self.canvas_image = self.canvas.create_image(
            0, 0, anchor=NW, image=image)

        # create input
        self.entry = Entry(self.frame, width='600', font=('Verdana', 30))
        self.entry.pack()

        return width, height

    def load_files(self):
        self.input_dir = self.entry_folder.get()
        self.output_dir = self.entry_output_folder.get()
        self.files = []
        for filename in os.listdir(self.input_dir):
            if filename.endswith('.jpg') or filename.endswith('.png'):
                file_path = os.path.join(self.input_dir, filename)
                self.files.append(file_path)

        # reload records
        self.read_output_file()

        # reload canvas
        if self.first_load:
            width, height = self.load_canvas()
            self.first_load = False
            self.update_frame_size(width, height)

        # set value of first entry if any (must load after canvas)
        self.set_default_label()

    def get_output_filename(self):
        out_file_name = os.path.basename(os.path.normpath(self.input_dir))
        out_file = os.path.join(self.output_dir, out_file_name + '.txt')
        return out_file

    def key_left(self, event):
        self.index = self.index - 1
        if self.index < 0:
            self.index = len(self.files) - 1

        self.load_navigate_image()

    def key_up(self, event):
        if event.keycode == 36 and len(self.files):  # enter
            self.write_file()
            self.key_right(event)

        if event.keycode == 9:  # esc
            self.master.destroy()

    def key_right(self, event):
        self.index = self.index + 1
        if self.index > len(self.files) - 1:
            self.index = 0

        self.load_navigate_image()

    def write_file(self):
        label = self.entry.get()
        image_path = self.files[self.index]

        index = self.find_record(image_path)

        if index == -1:
            self.records.append(Record(image_path, label))
        else:
            self.records[index] = Record(image_path, label)

        # dump to files
        self.dump_to_file()

    def mouse_left(self, event):
        pass

    def load_navigate_image(self):
        if len(self.files) == 0:
            return

        image, width, height = self.set_image()

        self.update_frame_size(width, height)

        self.image = image
        self.canvas.itemconfig(self.canvas_image, image=self.image)

        # set old label
        self.set_default_label()

    def set_default_label(self):
        image_path = self.files[self.index]
        index = self.find_record(image_path)

        label = ''

        if index != -1:
            record = self.records[index]
            label = record.label
        self.entry.delete(0, END)
        self.entry.insert(0, label)

    def dump_to_file(self):
        out_file = self.get_output_filename()
        file = open(out_file, 'w')

        content = ''
        for record in self.records:
            content = content + '{}\t{}\n'.format(record.file_path, record.label)

        file.write(content)
        file.close()

    def update_frame_size(self, width, height):
        self.master.geometry('{}x{}'.format(width, height + 40 * 3))
        self.canvas.config(width=width, height=height)
        self.entry.config(width=width)

    def set_image(self):

        if len(self.files) == 0:
            return

        image_path = self.files[self.index]

        # resize image
        image = cv2.imread(image_path)
        image = BasicGUI.resize(image, height=600)

        # turn to correct color space
        b, g, r = cv2.split(image)
        image = cv2.merge((r, g, b))

        # convert to tk image
        h, w, c = image.shape
        image = Image.fromarray(image)
        image = ImageTk.PhotoImage(image)
        return image, w, h

    @staticmethod
    def resize(image, width=None, height=None, inter=cv2.INTER_AREA):
        (h, w) = image.shape[:2]
        if width is None and height is None:
            return image

        if width is None:
            r = height / float(h)
            dim = (int(w * r), height)
        else:
            r = width / float(w)
            dim = (width, int(h * r))

        return cv2.resize(image, dim, interpolation=inter)

    # parse label files and check for duplicate file path for update
    def read_output_file(self):
        file_path = self.get_output_filename()

        if len(self.files) == 0 or not os.path.exists(file_path):
            return

        file = open(file_path, 'r')
        content = file.read()

        lines = content.split('\n')
        records = list(map(lambda line: line.split('\t'), lines))
        records = list(filter(lambda record: len(record) == 2, records))
        records = list(map(lambda record: Record(record[0], record[1]), records))

        self.records = records

    def find_record(self, file_path) -> int:
        for idx, val in enumerate(self.records):
            if val.file_path != file_path:
                continue
            return idx
        return -1


def main():
    root = Tk()
    root.geometry('500x680')

    gui = BasicGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
